# ---
# purpose: Tests for the Decision Engine layer (v1.1 humidity delta, v0.6
#          presence) and DecisionLog (v0.4, decision logging).
# version: 1.1.0
# ---

import sys
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for _mod in (
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.helpers",
    "homeassistant.helpers.event",
):
    sys.modules.setdefault(_mod, MagicMock())

from custom_components.shower_guard.decision import Decision, DecisionEngine, DecisionLog
from custom_components.shower_guard.session import SessionState

T0 = datetime(2026, 1, 1, 8, 0, 0)


def t(seconds: int = 0) -> datetime:
    return T0 + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# No active session
# ---------------------------------------------------------------------------

def test_idle_state_water_available():
    """No active session -> water remains available."""
    engine = DecisionEngine()
    result = engine.evaluate(SessionState.IDLE, active_since=None, now=t())
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.session_duration_seconds == 0.0


# ---------------------------------------------------------------------------
# Humidity delta (v1.1) — the sole cutoff trigger besides presence
# ---------------------------------------------------------------------------

def test_humidity_delta_within_threshold_stays_available():
    """A small humidity rise stays available."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(60),
        humidity=85.0,
        active_since_humidity=75.0,
    )
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.humidity_delta == 10.0


def test_humidity_delta_at_threshold_cuts_water():
    """A humidity rise meeting the threshold cuts water immediately,
    regardless of how little time has elapsed."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(5),
        humidity=90.0,
        active_since_humidity=75.0,
    )
    assert result.decision is Decision.WATER_CUT
    assert result.humidity_delta == 15.0
    assert "humidity rose" in result.reason.lower()


def test_cold_shower_low_delta_stays_available_indefinitely():
    """A cold shower (low humidity delta) is not penalized just for running
    long — there is no duration-based fallback."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(3600),  # an hour later
        humidity=77.0,
        active_since_humidity=75.0,
    )
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.humidity_delta == 2.0


def test_custom_max_humidity_delta():
    """Engine respects a custom max_humidity_delta value."""
    engine = DecisionEngine(max_humidity_delta=5.0)
    below = engine.evaluate(
        SessionState.ACTIVE, active_since=t(0), now=t(60), humidity=74.0, active_since_humidity=70.0
    )
    at_threshold = engine.evaluate(
        SessionState.ACTIVE, active_since=t(0), now=t(60), humidity=75.0, active_since_humidity=70.0
    )
    assert below.decision is Decision.WATER_AVAILABLE
    assert at_threshold.decision is Decision.WATER_CUT


def test_missing_humidity_data_has_no_cutoff():
    """Without humidity/active_since_humidity, there is no trigger at all
    (besides presence) — water stays available."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(SessionState.ACTIVE, active_since=t(0), now=t(300))
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.humidity_delta is None


def test_cooldown_state_delta_still_evaluated():
    """COOLDOWN counts toward the same session — delta still applies."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.COOLDOWN,
        active_since=t(0),
        now=t(30),
        humidity=91.0,
        active_since_humidity=75.0,
    )
    assert result.decision is Decision.WATER_CUT


def test_decision_result_str_is_readable():
    """DecisionResult.__str__ includes decision, state, duration, and delta."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(300),
        humidity=85.0,
        active_since_humidity=75.0,
    )
    text = str(result)
    assert "WATER_AVAILABLE" in text
    assert "active" in text
    assert "300" in text
    assert "delta=10.0" in text


# ---------------------------------------------------------------------------
# Presence (v0.6, optional) — takes priority over humidity delta
# ---------------------------------------------------------------------------

def test_presence_absent_cuts_water_immediately():
    """presence=False cuts water even with a tiny humidity delta."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(5),
        humidity=76.0,
        active_since_humidity=75.0,
        presence=False,
    )
    assert result.decision is Decision.WATER_CUT
    assert "presence" in result.reason.lower()


def test_presence_present_falls_back_to_humidity_delta_policy():
    """presence=True behaves the same as presence=None (delta policy)."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    within = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(60),
        humidity=85.0,
        active_since_humidity=75.0,
        presence=True,
    )
    exceeded = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(60),
        humidity=91.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert within.decision is Decision.WATER_AVAILABLE
    assert exceeded.decision is Decision.WATER_CUT


def test_presence_absent_but_idle_stays_water_available():
    """No active session -> water available regardless of presence."""
    engine = DecisionEngine()
    result = engine.evaluate(
        SessionState.IDLE, active_since=None, now=t(0), presence=False
    )
    assert result.decision is Decision.WATER_AVAILABLE


# ---------------------------------------------------------------------------
# DecisionLog (v0.4)
# ---------------------------------------------------------------------------

def test_decision_log_starts_empty():
    """A fresh DecisionLog has no entries."""
    log = DecisionLog(max_entries=10)
    assert len(log) == 0
    assert log.entries == ()
    assert log.last is None


def test_decision_log_records_entries_in_order():
    """Recorded entries are kept oldest-first."""
    engine = DecisionEngine()
    log = DecisionLog(max_entries=10)

    r1 = engine.evaluate(SessionState.IDLE, active_since=None, now=t(0))
    r2 = engine.evaluate(SessionState.ACTIVE, active_since=t(0), now=t(60))
    log.record(r1)
    log.record(r2)

    assert len(log) == 2
    assert log.entries == (r1, r2)
    assert log.last is r2


def test_decision_log_evicts_oldest_beyond_max_entries():
    """Once max_entries is exceeded, the oldest entries are dropped."""
    engine = DecisionEngine()
    log = DecisionLog(max_entries=2)

    r1 = engine.evaluate(SessionState.IDLE, active_since=None, now=t(0))
    r2 = engine.evaluate(SessionState.IDLE, active_since=None, now=t(1))
    r3 = engine.evaluate(SessionState.IDLE, active_since=None, now=t(2))
    for r in (r1, r2, r3):
        log.record(r)

    assert len(log) == 2
    assert log.entries == (r2, r3)
    assert log.last is r3


if __name__ == "__main__":
    tests = [
        test_idle_state_water_available,
        test_humidity_delta_within_threshold_stays_available,
        test_humidity_delta_at_threshold_cuts_water,
        test_cold_shower_low_delta_stays_available_indefinitely,
        test_custom_max_humidity_delta,
        test_missing_humidity_data_has_no_cutoff,
        test_cooldown_state_delta_still_evaluated,
        test_decision_result_str_is_readable,
        test_presence_absent_cuts_water_immediately,
        test_presence_present_falls_back_to_humidity_delta_policy,
        test_presence_absent_but_idle_stays_water_available,
        test_decision_log_starts_empty,
        test_decision_log_records_entries_in_order,
        test_decision_log_evicts_oldest_beyond_max_entries,
    ]
    passed = 0
    failed = 0
    for t_fn in tests:
        try:
            t_fn()
            print(f"PASS  {t_fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {t_fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")

