# ---
# purpose: Tests for the Decision Engine layer (v0.3 dry run, v0.6 presence)
#          and DecisionLog (v0.4, decision logging).
# version: 0.6.0
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


def test_idle_state_water_available():
    """No active session -> water remains available."""
    engine = DecisionEngine(max_session_seconds=900)
    result = engine.evaluate(SessionState.IDLE, active_since=None, now=t())
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.session_duration_seconds == 0.0


def test_active_within_limit_water_available():
    """Session running under the max duration -> water remains available."""
    engine = DecisionEngine(max_session_seconds=900)
    result = engine.evaluate(SessionState.ACTIVE, active_since=t(0), now=t(300))
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.session_duration_seconds == 300.0


def test_active_exceeds_limit_water_cut():
    """Session running past the max duration -> water is cut."""
    engine = DecisionEngine(max_session_seconds=900)
    result = engine.evaluate(SessionState.ACTIVE, active_since=t(0), now=t(900))
    assert result.decision is Decision.WATER_CUT
    assert result.session_duration_seconds == 900.0
    assert "exceeded max duration" in result.reason


def test_cooldown_exceeding_limit_water_cut():
    """COOLDOWN counts toward the same session — still cut if over limit."""
    engine = DecisionEngine(max_session_seconds=900)
    result = engine.evaluate(SessionState.COOLDOWN, active_since=t(0), now=t(1000))
    assert result.decision is Decision.WATER_CUT


def test_custom_max_session_seconds():
    """Engine respects a custom max_session_seconds value."""
    engine = DecisionEngine(max_session_seconds=60)
    assert engine.evaluate(SessionState.ACTIVE, active_since=t(0), now=t(59)).decision is Decision.WATER_AVAILABLE
    assert engine.evaluate(SessionState.ACTIVE, active_since=t(0), now=t(60)).decision is Decision.WATER_CUT


def test_decision_result_str_is_readable():
    """DecisionResult.__str__ includes decision, state, and duration."""
    engine = DecisionEngine(max_session_seconds=900)
    result = engine.evaluate(SessionState.ACTIVE, active_since=t(0), now=t(300))
    text = str(result)
    assert "WATER_AVAILABLE" in text
    assert "active" in text
    assert "300" in text


# ---------------------------------------------------------------------------
# Presence (v0.6, optional)
# ---------------------------------------------------------------------------

def test_presence_absent_cuts_water_immediately():
    """presence=False cuts water even well within max_session_seconds."""
    engine = DecisionEngine(max_session_seconds=900)
    result = engine.evaluate(
        SessionState.ACTIVE, active_since=t(0), now=t(5), presence=False
    )
    assert result.decision is Decision.WATER_CUT
    assert "presence" in result.reason.lower()


def test_presence_present_falls_back_to_duration_policy():
    """presence=True behaves the same as the default duration-only policy."""
    engine = DecisionEngine(max_session_seconds=900)
    within = engine.evaluate(
        SessionState.ACTIVE, active_since=t(0), now=t(300), presence=True
    )
    exceeded = engine.evaluate(
        SessionState.ACTIVE, active_since=t(0), now=t(900), presence=True
    )
    assert within.decision is Decision.WATER_AVAILABLE
    assert exceeded.decision is Decision.WATER_CUT


def test_presence_unknown_falls_back_to_duration_policy():
    """presence=None (default — no sensor configured/unknown state) behaves
    identically to the pre-v0.6 duration-only policy."""
    engine = DecisionEngine(max_session_seconds=900)
    result = engine.evaluate(SessionState.ACTIVE, active_since=t(0), now=t(300))
    assert result.decision is Decision.WATER_AVAILABLE


def test_presence_absent_but_idle_stays_water_available():
    """No active session -> water available regardless of presence."""
    engine = DecisionEngine(max_session_seconds=900)
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
    engine = DecisionEngine(max_session_seconds=900)
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
    engine = DecisionEngine(max_session_seconds=900)
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
        test_active_within_limit_water_available,
        test_active_exceeds_limit_water_cut,
        test_cooldown_exceeding_limit_water_cut,
        test_custom_max_session_seconds,
        test_decision_result_str_is_readable,
        test_presence_absent_cuts_water_immediately,
        test_presence_present_falls_back_to_duration_policy,
        test_presence_unknown_falls_back_to_duration_policy,
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
