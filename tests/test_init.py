# ---
# purpose: Tests for the Sensor Layer -> Session Detection wiring (v0.2).
# version: 0.2.0
# ---

import asyncio
import sys
import os
from types import SimpleNamespace
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

import custom_components.shower_guard as shower_guard
from custom_components.shower_guard.const import DOMAIN
from custom_components.shower_guard.session import SessionState


def make_hass():
    return SimpleNamespace(data={})


def patch_track_state_change(monkeypatch_target=shower_guard):
    """Replace async_track_state_change_event with a spy and return the capture dict."""
    captured = {}

    def fake_track(hass_arg, entity_ids, callback):
        captured["hass"] = hass_arg
        captured["entity_ids"] = entity_ids
        captured["callback"] = callback
        return lambda: None

    monkeypatch_target.async_track_state_change_event = fake_track
    return captured


# ---------------------------------------------------------------------------
# No / incomplete configuration -> no-op, but setup still succeeds
# ---------------------------------------------------------------------------

def test_async_setup_without_config_is_noop():
    hass = make_hass()
    result = asyncio.run(shower_guard.async_setup(hass, {}))
    assert result is True
    assert "detector" not in hass.data.get(DOMAIN, {})


def test_async_setup_without_humidity_sensor_is_noop():
    hass = make_hass()
    result = asyncio.run(shower_guard.async_setup(hass, {DOMAIN: {}}))
    assert result is True
    assert "detector" not in hass.data.get(DOMAIN, {})


# ---------------------------------------------------------------------------
# Valid configuration -> detector created and listener registered
# ---------------------------------------------------------------------------

def test_async_setup_registers_state_listener():
    hass = make_hass()
    captured = patch_track_state_change()

    result = asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    assert result is True
    assert captured["entity_ids"] == ["sensor.bathroom_humidity"]
    assert hass.data[DOMAIN]["detector"].state is SessionState.IDLE


def test_async_setup_uses_custom_threshold_and_cooldown():
    hass = make_hass()
    patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "humidity_threshold": 60.0,
                    "cooldown_seconds": 30,
                }
            },
        )
    )

    detector = hass.data[DOMAIN]["detector"]
    assert detector.humidity_threshold == 60.0
    assert detector.cooldown_seconds == 30


# ---------------------------------------------------------------------------
# Humidity change callback feeds the Session Detection layer
# ---------------------------------------------------------------------------

def test_humidity_callback_feeds_detector():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    detector = hass.data[DOMAIN]["detector"]
    assert detector.state is SessionState.IDLE

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(captured["callback"](event))

    assert detector.state is SessionState.ACTIVE


def test_humidity_callback_ignores_unavailable_state():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    detector = hass.data[DOMAIN]["detector"]
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="unavailable")})
    asyncio.run(captured["callback"](event))

    assert detector.state is SessionState.IDLE


def test_humidity_callback_ignores_non_numeric_state():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    detector = hass.data[DOMAIN]["detector"]
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="not_a_number")})
    asyncio.run(captured["callback"](event))

    assert detector.state is SessionState.IDLE


def test_humidity_callback_ignores_missing_new_state():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    detector = hass.data[DOMAIN]["detector"]
    event = SimpleNamespace(data={"new_state": None})
    asyncio.run(captured["callback"](event))

    assert detector.state is SessionState.IDLE


if __name__ == "__main__":
    tests = [
        test_async_setup_without_config_is_noop,
        test_async_setup_without_humidity_sensor_is_noop,
        test_async_setup_registers_state_listener,
        test_async_setup_uses_custom_threshold_and_cooldown,
        test_humidity_callback_feeds_detector,
        test_humidity_callback_ignores_unavailable_state,
        test_humidity_callback_ignores_non_numeric_state,
        test_humidity_callback_ignores_missing_new_state,
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
