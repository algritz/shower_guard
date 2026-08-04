# ---
# purpose: Tests for the Replay Engine (v0.5, v1.1 humidity delta + optional
#          duration fallback).
# version: 1.1.0
# ---

import sys
import os
import csv
import tempfile
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

from custom_components.shower_guard.decision import Decision
from custom_components.shower_guard.replay import load_readings_from_csv, replay
from custom_components.shower_guard.session import SessionEvent, SessionState

T0 = datetime(2026, 1, 1, 8, 0, 0)


def t(seconds: int = 0) -> datetime:
    return T0 + timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# replay() — reuses the exact same Session Detection / Decision Engine classes
# ---------------------------------------------------------------------------

def test_replay_empty_readings_produces_empty_result():
    result = replay([])
    assert result.state_changes == []
    assert len(result.decision_log) == 0


def test_replay_full_session_lifecycle():
    """Mirrors test_session.test_full_session_lifecycle, driven via replay()."""
    readings = [
        (t(0), 80.0),     # shower starts
        (t(300), 85.0),   # shower running
        (t(600), 65.0),   # shower stops -> COOLDOWN silently
        (t(900), 65.0),   # cooldown expires -> ENDED
    ]

    result = replay(readings, cooldown_seconds=300)

    assert [c.event for c in result.state_changes] == [
        SessionEvent.STARTED,
        SessionEvent.ENDED,
    ]
    assert len(result.decision_log) == len(readings)


def test_replay_records_a_decision_per_reading():
    readings = [(t(0), 80.0), (t(60), 82.0), (t(120), 60.0)]
    result = replay(readings)
    assert len(result.decision_log) == 3


def test_replay_water_cut_when_humidity_delta_exceeds_threshold_with_presence():
    """As of ADR-0003, delta alone isn't enough in replay either — presence
    readings must be supplied and confirm within the window."""
    readings = [(t(0), 75.0), (t(60), 92.0)]
    presence_readings = [(t(0), True)]
    result = replay(
        readings, max_humidity_delta=15.0, presence_readings=presence_readings
    )

    assert result.decision_log.last.decision is Decision.WATER_CUT


def test_replay_water_cut_does_not_happen_without_presence_data():
    """The same delta spike, with no presence_readings supplied at all,
    never confirms and stays available — matching production behavior with
    no presence_sensor configured."""
    readings = [(t(0), 75.0), (t(60), 92.0)]
    result = replay(readings, max_humidity_delta=15.0)

    assert result.decision_log.last.decision is Decision.WATER_AVAILABLE


def test_replay_presence_confirmation_tolerates_brief_gap():
    """Presence seen True once, then not reported again, still confirms a
    later delta-triggered cut within the confirmation window."""
    readings = [(t(0), 75.0), (t(50), 92.0)]
    presence_readings = [(t(0), True), (t(10), False)]
    result = replay(
        readings,
        max_humidity_delta=15.0,
        presence_confirmation_window_seconds=60.0,
        presence_readings=presence_readings,
    )

    assert result.decision_log.last.decision is Decision.WATER_CUT


def test_replay_duration_fallback_disabled_by_default():
    """Without max_session_seconds, a low-delta reading stays available even
    long after session start."""
    readings = [(t(0), 75.0), (t(3600), 77.0)]
    result = replay(readings, max_humidity_delta=15.0)

    assert result.decision_log.last.decision is Decision.WATER_AVAILABLE


def test_replay_duration_fallback_cuts_water_when_enabled():
    readings = [(t(0), 75.0), (t(900), 77.0)]
    result = replay(readings, max_humidity_delta=15.0, max_session_seconds=900)

    assert result.decision_log.last.decision is Decision.WATER_CUT


def test_replay_respects_custom_threshold_and_cooldown():
    readings = [(t(0), 60.0)]  # below default 75.0 threshold
    result = replay(readings, humidity_threshold=60.0)

    assert result.state_changes[0].event is SessionEvent.STARTED


def test_replay_decision_log_respects_size_limit():
    readings = [(t(i), 50.0) for i in range(5)]
    result = replay(readings, decision_log_size=2)

    assert len(result.decision_log) == 2


# ---------------------------------------------------------------------------
# load_readings_from_csv()
# ---------------------------------------------------------------------------

def test_load_readings_from_csv():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", newline="", delete=False
    ) as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "humidity"])
        writer.writerow([t(0).isoformat(), "80.0"])
        writer.writerow([t(60).isoformat(), "82.5"])
        path = f.name

    try:
        readings = load_readings_from_csv(path)
        assert readings == [(t(0), 80.0), (t(60), 82.5)]
    finally:
        os.remove(path)


def test_csv_readings_can_be_replayed():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", newline="", delete=False
    ) as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "humidity"])
        writer.writerow([t(0).isoformat(), "80.0"])
        writer.writerow([t(900).isoformat(), "85.0"])
        path = f.name

    try:
        readings = load_readings_from_csv(path)
        result = replay(readings)
        assert result.state_changes[0].event is SessionEvent.STARTED
    finally:
        os.remove(path)


if __name__ == "__main__":
    tests = [
        test_replay_empty_readings_produces_empty_result,
        test_replay_full_session_lifecycle,
        test_replay_records_a_decision_per_reading,
        test_replay_water_cut_when_humidity_delta_exceeds_threshold_with_presence,
        test_replay_water_cut_does_not_happen_without_presence_data,
        test_replay_presence_confirmation_tolerates_brief_gap,
        test_replay_duration_fallback_disabled_by_default,
        test_replay_duration_fallback_cuts_water_when_enabled,
        test_replay_respects_custom_threshold_and_cooldown,
        test_replay_decision_log_respects_size_limit,
        test_load_readings_from_csv,
        test_csv_readings_can_be_replayed,
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
