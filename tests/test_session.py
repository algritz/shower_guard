# ---
# purpose: Tests for the Session Detection layer (v0.2).
# version: 0.2.0
# ---

import sys
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for _mod in ("homeassistant", "homeassistant.core", "homeassistant.config_entries"):
    sys.modules.setdefault(_mod, MagicMock())

from custom_components.shower_guard.session import (
    SessionDetector,
    SessionEvent,
    SessionState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = datetime(2026, 1, 1, 8, 0, 0)

def t(seconds: int = 0) -> datetime:
    """Return T0 + seconds."""
    return T0 + timedelta(seconds=seconds)


def make_detector(threshold: float = 75.0, cooldown: int = 300) -> SessionDetector:
    return SessionDetector(humidity_threshold=threshold, cooldown_seconds=cooldown)


# ---------------------------------------------------------------------------
# IDLE state
# ---------------------------------------------------------------------------

def test_idle_low_humidity_no_change():
    """Low humidity in IDLE produces no state change."""
    d = make_detector()
    result = d.update(humidity=50.0, now=t())
    assert result is None
    assert d.state is SessionState.IDLE


def test_idle_at_threshold_starts_session():
    """Humidity exactly at threshold triggers session start."""
    d = make_detector(threshold=75.0)
    change = d.update(humidity=75.0, now=t())
    assert change is not None
    assert change.event is SessionEvent.STARTED
    assert change.previous is SessionState.IDLE
    assert change.current is SessionState.ACTIVE
    assert d.state is SessionState.ACTIVE


def test_idle_above_threshold_starts_session():
    """Humidity above threshold triggers session start."""
    d = make_detector()
    change = d.update(humidity=90.0, now=t())
    assert change is not None
    assert change.event is SessionEvent.STARTED
    assert d.state is SessionState.ACTIVE


# ---------------------------------------------------------------------------
# ACTIVE state
# ---------------------------------------------------------------------------

def test_active_stays_active_while_humid():
    """Sustained high humidity keeps state ACTIVE with no event."""
    d = make_detector()
    d.update(humidity=80.0, now=t())          # → ACTIVE
    result = d.update(humidity=85.0, now=t(60))
    assert result is None
    assert d.state is SessionState.ACTIVE


def test_active_enters_cooldown_on_humidity_drop():
    """Humidity drop from ACTIVE enters COOLDOWN silently (no event yet)."""
    d = make_detector()
    d.update(humidity=80.0, now=t())          # → ACTIVE
    result = d.update(humidity=60.0, now=t(10))
    assert result is None
    assert d.state is SessionState.COOLDOWN


# ---------------------------------------------------------------------------
# COOLDOWN state
# ---------------------------------------------------------------------------

def test_cooldown_ends_session_after_elapsed():
    """Cooldown expiry emits ENDED and returns to IDLE."""
    d = make_detector(cooldown=300)
    d.update(humidity=80.0, now=t())          # → ACTIVE
    d.update(humidity=60.0, now=t(10))        # → COOLDOWN

    # Just before expiry — still in cooldown.
    result = d.update(humidity=60.0, now=t(10 + 299))
    assert result is None
    assert d.state is SessionState.COOLDOWN

    # At expiry — session ends.
    change = d.update(humidity=60.0, now=t(10 + 300))
    assert change is not None
    assert change.event is SessionEvent.ENDED
    assert change.current is SessionState.IDLE
    assert d.state is SessionState.IDLE


def test_cooldown_resumes_on_humidity_rise():
    """Humidity rising during cooldown emits RESUMED and returns to ACTIVE."""
    d = make_detector(cooldown=300)
    d.update(humidity=80.0, now=t())          # → ACTIVE
    d.update(humidity=60.0, now=t(10))        # → COOLDOWN

    change = d.update(humidity=80.0, now=t(60))
    assert change is not None
    assert change.event is SessionEvent.RESUMED
    assert change.current is SessionState.ACTIVE
    assert d.state is SessionState.ACTIVE


def test_cooldown_not_expired_no_event():
    """Low humidity within cooldown window produces no event."""
    d = make_detector(cooldown=300)
    d.update(humidity=80.0, now=t())          # → ACTIVE
    d.update(humidity=60.0, now=t(10))        # → COOLDOWN

    result = d.update(humidity=60.0, now=t(100))
    assert result is None
    assert d.state is SessionState.COOLDOWN


# ---------------------------------------------------------------------------
# StateChange dataclass
# ---------------------------------------------------------------------------

def test_state_change_str_is_readable():
    """StateChange.__str__ should include event name, states, and humidity."""
    d = make_detector()
    change = d.update(humidity=80.0, now=T0)
    assert "STARTED" in str(change)
    assert "idle" in str(change)
    assert "active" in str(change)
    assert "80.0" in str(change)


# ---------------------------------------------------------------------------
# Custom threshold / cooldown
# ---------------------------------------------------------------------------

def test_custom_threshold():
    """Detector respects a custom humidity threshold."""
    d = make_detector(threshold=60.0)
    assert d.update(humidity=59.9, now=t()) is None
    change = d.update(humidity=60.0, now=t(1))
    assert change is not None
    assert change.event is SessionEvent.STARTED


def test_custom_cooldown():
    """Detector respects a custom cooldown duration."""
    d = make_detector(cooldown=30)
    d.update(humidity=80.0, now=t())     # → ACTIVE
    d.update(humidity=50.0, now=t(1))    # → COOLDOWN

    assert d.update(humidity=50.0, now=t(29)) is None   # not yet
    change = d.update(humidity=50.0, now=t(31))         # expired
    assert change is not None
    assert change.event is SessionEvent.ENDED


# ---------------------------------------------------------------------------
# Full session lifecycle
# ---------------------------------------------------------------------------

def test_full_session_lifecycle():
    """Walk through a complete session: start → cooldown → end."""
    d = make_detector(cooldown=300)

    # Shower starts
    c1 = d.update(humidity=80.0, now=t(0))
    assert c1.event is SessionEvent.STARTED

    # Shower running
    assert d.update(humidity=85.0, now=t(300)) is None

    # Shower stops
    assert d.update(humidity=65.0, now=t(600)) is None   # → COOLDOWN silently
    assert d.state is SessionState.COOLDOWN

    # Cooldown expires
    c2 = d.update(humidity=65.0, now=t(900))
    assert c2.event is SessionEvent.ENDED

    # Back to idle
    assert d.state is SessionState.IDLE


if __name__ == "__main__":
    tests = [
        test_idle_low_humidity_no_change,
        test_idle_at_threshold_starts_session,
        test_idle_above_threshold_starts_session,
        test_active_stays_active_while_humid,
        test_active_enters_cooldown_on_humidity_drop,
        test_cooldown_ends_session_after_elapsed,
        test_cooldown_resumes_on_humidity_rise,
        test_cooldown_not_expired_no_event,
        test_state_change_str_is_readable,
        test_custom_threshold,
        test_custom_cooldown,
        test_full_session_lifecycle,
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
