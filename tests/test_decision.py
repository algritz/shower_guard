# ---
# purpose: Tests for the Decision Engine layer (v0.3, dry run).
# version: 0.3.0
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

from custom_components.shower_guard.decision import Decision, DecisionEngine
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


if __name__ == "__main__":
    tests = [
        test_idle_state_water_available,
        test_active_within_limit_water_available,
        test_active_exceeds_limit_water_cut,
        test_cooldown_exceeding_limit_water_cut,
        test_custom_max_session_seconds,
        test_decision_result_str_is_readable,
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
