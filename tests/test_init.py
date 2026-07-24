# ---
# purpose: Tests for the Sensor Layer -> Session Detection -> Decision Engine
#          -> DecisionLog wiring (v0.2/v0.3/v0.4), plus the optional presence
#          sensor wiring (v0.6).
# version: 0.6.0
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
from custom_components.shower_guard.decision import Decision
from custom_components.shower_guard.session import SessionState


def make_hass():
    return SimpleNamespace(data={})


def patch_track_state_change(monkeypatch_target=shower_guard):
    """Replace async_track_state_change_event with a spy and return the
    capture dict. Supports multiple registrations (e.g. humidity + presence);
    top-level keys reflect the most recent registration for backward
    compatibility, while captured["registrations"] lists every call."""
    captured = {"registrations": []}

    def fake_track(hass_arg, entity_ids, callback):
        registration = {"hass": hass_arg, "entity_ids": entity_ids, "callback": callback}
        captured["registrations"].append(registration)
        captured.update(registration)
        return lambda: None

    monkeypatch_target.async_track_state_change_event = fake_track
    return captured


def callback_for(captured, entity_id):
    """Look up the registered callback for a specific entity_id."""
    for registration in captured["registrations"]:
        if entity_id in registration["entity_ids"]:
            return registration["callback"]
    raise AssertionError(f"No listener registered for {entity_id}")


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
    assert hass.data[DOMAIN]["decision_engine"] is not None
    assert hass.data[DOMAIN]["decision_log"] is not None
    assert len(hass.data[DOMAIN]["decision_log"]) == 0
    assert hass.data[DOMAIN]["last_decision"] is None
    assert hass.data[DOMAIN]["presence"] is None
    assert len(captured["registrations"]) == 1  # no presence_sensor configured


def test_async_setup_uses_custom_decision_log_size():
    hass = make_hass()
    patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "decision_log_size": 5,
                }
            },
        )
    )

    decision_log = hass.data[DOMAIN]["decision_log"]
    assert decision_log.max_entries == 5


def test_async_setup_uses_custom_max_session_seconds():
    hass = make_hass()
    patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_session_seconds": 60,
                }
            },
        )
    )

    engine = hass.data[DOMAIN]["decision_engine"]
    assert engine.max_session_seconds == 60


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


# ---------------------------------------------------------------------------
# Decision Engine wiring (v0.3, dry run — logged only, no actuator call)
# ---------------------------------------------------------------------------

def test_humidity_callback_records_water_available_decision():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision is not None
    assert last_decision.decision is Decision.WATER_AVAILABLE


def test_humidity_callback_records_water_cut_when_session_exceeds_limit():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_session_seconds": 0,
                }
            },
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT


# ---------------------------------------------------------------------------
# DecisionLog wiring (v0.4 — every evaluation is recorded, not just changes)
# ---------------------------------------------------------------------------

def test_humidity_callback_records_every_evaluation_in_decision_log():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    decision_log = hass.data[DOMAIN]["decision_log"]

    for state in ("80.0", "82.0", "60.0"):
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=state)})
        asyncio.run(captured["callback"](event))

    assert len(decision_log) == 3
    assert decision_log.last is hass.data[DOMAIN]["last_decision"]


# ---------------------------------------------------------------------------
# Presence sensor wiring (v0.6, optional)
# ---------------------------------------------------------------------------

def test_async_setup_registers_presence_listener_when_configured():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                }
            },
        )
    )

    assert len(captured["registrations"]) == 2
    callback_for(captured, "binary_sensor.bathroom_presence")  # raises if missing


def test_presence_absent_cuts_water_immediately_during_active_session():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")
    presence_callback = callback_for(captured, "binary_sensor.bathroom_presence")

    # Shower starts — no presence info yet, so duration-based policy applies.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(humidity_callback(event))
    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE

    # Presence sensor reports the room is now empty — immediate cut, even
    # though max_session_seconds has not elapsed and no new humidity arrived.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="off")})
    asyncio.run(presence_callback(event))

    assert hass.data[DOMAIN]["presence"] is False
    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT
    assert "presence" in last_decision.reason.lower()


def test_presence_callback_ignores_unknown_state():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                }
            },
        )
    )

    presence_callback = callback_for(captured, "binary_sensor.bathroom_presence")
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="unknown")})
    asyncio.run(presence_callback(event))

    assert hass.data[DOMAIN]["presence"] is None


def test_no_presence_listener_when_not_configured():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    assert len(captured["registrations"]) == 1
    assert "remove_presence_listener" not in hass.data[DOMAIN]


if __name__ == "__main__":
    tests = [
        test_async_setup_without_config_is_noop,
        test_async_setup_without_humidity_sensor_is_noop,
        test_async_setup_registers_state_listener,
        test_async_setup_uses_custom_decision_log_size,
        test_async_setup_uses_custom_max_session_seconds,
        test_async_setup_uses_custom_threshold_and_cooldown,
        test_humidity_callback_feeds_detector,
        test_humidity_callback_ignores_unavailable_state,
        test_humidity_callback_ignores_non_numeric_state,
        test_humidity_callback_ignores_missing_new_state,
        test_humidity_callback_records_water_available_decision,
        test_humidity_callback_records_water_cut_when_session_exceeds_limit,
        test_humidity_callback_records_every_evaluation_in_decision_log,
        test_async_setup_registers_presence_listener_when_configured,
        test_presence_absent_cuts_water_immediately_during_active_session,
        test_presence_callback_ignores_unknown_state,
        test_no_presence_listener_when_not_configured,
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
