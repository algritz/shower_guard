# ---
# purpose: Tests for the Decision Engine layer — humidity delta gated by
#          presence confirmation (v1.4, ADR-0003), the confirmation latching
#          per delta baseline so a later presence gap can't alone un-confirm
#          an already-cut decision (v1.8, ADR-0007), optional duration
#          fallback (independent of presence), and DecisionLog (v0.4).
# version: 1.8.0
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
    "homeassistant.const",
    "homeassistant.helpers",
    "homeassistant.helpers.event",
):
    sys.modules.setdefault(_mod, MagicMock())

sys.modules["homeassistant.const"].STATE_UNKNOWN = "unknown"

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
# Humidity delta below threshold -> always available regardless of presence
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
        presence=True,
    )
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.humidity_delta == 10.0


def test_cold_shower_low_delta_stays_available_indefinitely():
    """A cold shower (low humidity delta) is not penalized just for running
    long, even with presence confirmed — delta must exceed the threshold
    before presence is even considered."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(3600),  # an hour later
        humidity=77.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.humidity_delta == 2.0


def test_missing_humidity_data_has_no_cutoff():
    """Without humidity/active_since_humidity, there is no delta trigger at
    all, regardless of presence — water stays available."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE, active_since=t(0), now=t(300), presence=True
    )
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.humidity_delta is None


# ---------------------------------------------------------------------------
# Humidity delta exceeded + presence confirmed (ADR-0003) -> cut
# ---------------------------------------------------------------------------

def test_delta_exceeded_with_presence_true_cuts_water():
    """Delta at/above threshold AND presence currently True -> cut."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(5),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert result.decision is Decision.WATER_CUT
    assert result.humidity_delta == 15.0
    assert "humidity rose" in result.reason.lower()
    assert "presence confirmed" in result.reason.lower()


def test_delta_exceeded_with_recent_last_presence_at_cuts_water():
    """Delta exceeded, presence currently unknown/False, but was seen True
    within the confirmation window -> still cuts (tolerates a brief mmWave
    dropout instead of requiring continuous detection at this instant)."""
    engine = DecisionEngine(
        max_humidity_delta=15.0, presence_confirmation_window_seconds=60.0
    )
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(90),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=False,
        last_presence_at=t(50),  # 40s before "now" — within the 60s window
    )
    assert result.decision is Decision.WATER_CUT
    assert "presence confirmed" in result.reason.lower()


def test_delta_exceeded_but_last_presence_outside_window_does_not_cut():
    """Delta exceeded, but the last confirmed presence is older than the
    confirmation window -> not confirmed, water stays available."""
    engine = DecisionEngine(
        max_humidity_delta=15.0, presence_confirmation_window_seconds=60.0
    )
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(200),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=False,
        last_presence_at=t(100),  # 100s before "now" — outside the 60s window
    )
    assert result.decision is Decision.WATER_AVAILABLE
    assert "not cutting" in result.reason.lower()


def test_delta_exceeded_with_presence_none_does_not_cut():
    """Delta exceeded but no presence data at all (no presence sensor
    configured) -> never confirmed, water stays available. This is the key
    behavior change from the old delta-alone-cuts policy (ADR-0003)."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(5),
        humidity=90.0,
        active_since_humidity=75.0,
    )
    assert result.decision is Decision.WATER_AVAILABLE
    assert result.humidity_delta == 15.0
    assert "not cutting" in result.reason.lower()


def test_delta_exceeded_with_presence_false_and_no_last_presence_does_not_cut():
    """Delta exceeded, presence explicitly False, and no prior confirmation
    on record -> not confirmed, stays available."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(5),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=False,
    )
    assert result.decision is Decision.WATER_AVAILABLE


def test_custom_max_humidity_delta_still_requires_presence():
    """Engine respects a custom max_humidity_delta value, and the presence
    gate still applies at the custom threshold."""
    engine = DecisionEngine(max_humidity_delta=5.0)
    below = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(60),
        humidity=74.0,
        active_since_humidity=70.0,
        presence=True,
    )
    at_threshold_no_presence = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(60),
        humidity=75.0,
        active_since_humidity=70.0,
    )
    at_threshold_with_presence = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(60),
        humidity=75.0,
        active_since_humidity=70.0,
        presence=True,
    )
    assert below.decision is Decision.WATER_AVAILABLE
    assert at_threshold_no_presence.decision is Decision.WATER_AVAILABLE
    assert at_threshold_with_presence.decision is Decision.WATER_CUT


def test_cooldown_state_delta_still_evaluated_with_presence():
    """COOLDOWN counts toward the same session — delta+presence still
    applies."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.COOLDOWN,
        active_since=t(0),
        now=t(30),
        humidity=91.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert result.decision is Decision.WATER_CUT


# ---------------------------------------------------------------------------
# Presence confirmation latch (ADR-0007) — a later momentary presence gap
# must not alone flip an already-confirmed cut back to available, while the
# delta is still exceeded. Fixes real-world mmWave dropout flapping.
# ---------------------------------------------------------------------------

def test_latch_keeps_water_cut_through_a_later_presence_gap():
    """Once presence has been confirmed for this baseline, a later gap
    longer than presence_confirmation_window_seconds does not, by itself,
    flip the decision back to available while delta is still exceeded —
    this is the real mmWave dropout pattern (multiple >60s gaps during a
    single continuous, attended shower)."""
    engine = DecisionEngine(
        max_humidity_delta=15.0, presence_confirmation_window_seconds=60.0
    )

    # Presence confirmed now -> cuts, and latches.
    first = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(30),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert first.decision is Decision.WATER_CUT

    # Presence sensor now reads False, and the last confirmed sighting is
    # well outside the 60s window — under the pre-ADR-0007 logic this would
    # flip back to available. With the latch, it must not.
    second = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(200),
        humidity=91.0,
        active_since_humidity=75.0,
        presence=False,
        last_presence_at=t(30),  # 170s ago — well outside the 60s window
    )
    assert second.decision is Decision.WATER_CUT
    assert "latched" in second.reason.lower()


def test_latch_does_not_apply_before_first_confirmation():
    """The latch only helps once presence has actually been confirmed at
    least once for this baseline — it doesn't pre-emptively assume
    confirmation. Matches existing unconfirmed-delta behavior exactly."""
    engine = DecisionEngine(
        max_humidity_delta=15.0, presence_confirmation_window_seconds=60.0
    )
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(200),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=False,
        last_presence_at=t(100),  # outside window, and never confirmed before
    )
    assert result.decision is Decision.WATER_AVAILABLE


def test_latch_resets_on_new_baseline_after_resumed():
    """A fresh delta baseline (simulating a sibling's RESUMED shower, or any
    new active_since_humidity) requires presence to be confirmed again —
    the latch from the previous baseline does not carry over."""
    engine = DecisionEngine(
        max_humidity_delta=15.0, presence_confirmation_window_seconds=60.0
    )

    first = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(30),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert first.decision is Decision.WATER_CUT

    # New baseline (e.g. RESUMED reset active_since_humidity to 60.0) and
    # presence is not currently/recently confirmed -> must not inherit the
    # previous baseline's latch.
    second = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(40),
        humidity=80.0,
        active_since_humidity=60.0,  # fresh baseline
        presence=False,
    )
    assert second.decision is Decision.WATER_AVAILABLE


def test_latch_does_not_override_genuine_delta_decline():
    """The latch only matters while delta is still exceeded — once humidity
    genuinely declines back under max_humidity_delta, water becomes
    available regardless of the latch, and does not itself reset the latch
    should delta rise again within the same baseline."""
    engine = DecisionEngine(
        max_humidity_delta=15.0, presence_confirmation_window_seconds=60.0
    )

    confirmed = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(30),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert confirmed.decision is Decision.WATER_CUT

    declined = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(200),
        humidity=78.0,  # delta now 3.0, below the 15.0 threshold
        active_since_humidity=75.0,
        presence=False,
    )
    assert declined.decision is Decision.WATER_AVAILABLE

    # Delta rises back above threshold within the *same* baseline, with no
    # fresh presence confirmation -> the still-latched confirmation from
    # earlier in this baseline still applies.
    rises_again = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(210),
        humidity=91.0,
        active_since_humidity=75.0,
        presence=False,
    )
    assert rises_again.decision is Decision.WATER_CUT


def test_latch_resets_when_session_returns_to_idle():
    """A session ending (IDLE) and a brand new one starting must require a
    fresh presence confirmation — the latch cannot leak across sessions."""
    engine = DecisionEngine(
        max_humidity_delta=15.0, presence_confirmation_window_seconds=60.0
    )

    confirmed = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(30),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert confirmed.decision is Decision.WATER_CUT

    idle = engine.evaluate(SessionState.IDLE, active_since=None, now=t(500))
    assert idle.decision is Decision.WATER_AVAILABLE

    new_session_unconfirmed = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(600),
        now=t(630),
        humidity=90.0,
        active_since_humidity=75.0,
        presence=False,
    )
    assert new_session_unconfirmed.decision is Decision.WATER_AVAILABLE


def test_regression_incident_no_longer_flaps_on_presence_gaps():
    """Regression test for the real mmWave flicker pattern logged during an
    actual continuous shower: presence toggles False/True every 20-100+
    seconds, with several gaps exceeding the 60s confirmation window. Once
    latched, the decision must not flap back to available on those gaps."""
    engine = DecisionEngine(
        max_humidity_delta=15.0, presence_confirmation_window_seconds=60.0
    )

    # Presence confirmed True at t(90) while delta is already well over
    # threshold -> cuts and latches.
    first = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(90),
        humidity=93.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert first.decision is Decision.WATER_CUT

    # A 104-second presence gap follows (matching the logged 03:42:22 ->
    # 03:44:06 gap) — old logic would flip this to available.
    gap = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(194),
        humidity=95.0,
        active_since_humidity=75.0,
        presence=False,
        last_presence_at=t(90),
    )
    assert gap.decision is Decision.WATER_CUT

    # A second, later gap of similar length — still latched.
    second_gap = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(400),
        humidity=96.0,
        active_since_humidity=75.0,
        presence=False,
        last_presence_at=t(300),
    )
    assert second_gap.decision is Decision.WATER_CUT


# ---------------------------------------------------------------------------
# Optional duration fallback — independent of presence (ADR-0003)
# ---------------------------------------------------------------------------

def test_duration_fallback_disabled_by_default():
    """Without max_session_seconds, a long session with a low delta stays
    available indefinitely — no implicit duration cap."""
    engine = DecisionEngine(max_humidity_delta=15.0)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(36000),
        humidity=77.0,
        active_since_humidity=75.0,
    )
    assert result.decision is Decision.WATER_AVAILABLE


def test_duration_fallback_cuts_water_when_enabled_without_presence():
    """With max_session_seconds set, a low-delta session is still capped
    once it runs past the configured duration — no presence data needed,
    since this is the safety net for exactly that case."""
    engine = DecisionEngine(max_humidity_delta=15.0, max_session_seconds=900)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(900),
        humidity=77.0,
        active_since_humidity=75.0,
    )
    assert result.decision is Decision.WATER_CUT
    assert "exceeded max duration" in result.reason.lower()


def test_duration_fallback_does_not_trigger_before_limit():
    """With max_session_seconds set, water stays available before the limit."""
    engine = DecisionEngine(max_humidity_delta=15.0, max_session_seconds=900)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(899),
        humidity=77.0,
        active_since_humidity=75.0,
    )
    assert result.decision is Decision.WATER_AVAILABLE


def test_confirmed_delta_cut_takes_priority_over_duration_fallback():
    """When both would trigger, the delta+presence policy is evaluated
    first and its reason wins."""
    engine = DecisionEngine(max_humidity_delta=15.0, max_session_seconds=900)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(900),
        humidity=95.0,
        active_since_humidity=75.0,
        presence=True,
    )
    assert result.decision is Decision.WATER_CUT
    assert "humidity rose" in result.reason.lower()


def test_unconfirmed_delta_falls_through_to_duration_fallback():
    """Delta exceeded but not presence-confirmed doesn't cut on its own —
    but if the duration fallback is also configured and elapsed, that still
    catches the session independently."""
    engine = DecisionEngine(max_humidity_delta=15.0, max_session_seconds=900)
    result = engine.evaluate(
        SessionState.ACTIVE,
        active_since=t(0),
        now=t(900),
        humidity=95.0,
        active_since_humidity=75.0,
        # no presence data at all
    )
    assert result.decision is Decision.WATER_CUT
    assert "exceeded max duration" in result.reason.lower()


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
# Idle stays available regardless of presence
# ---------------------------------------------------------------------------

def test_idle_stays_water_available_regardless_of_presence():
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
