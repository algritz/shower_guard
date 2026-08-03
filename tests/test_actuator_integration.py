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
    "homeassistant.const",
    "homeassistant.helpers",
    "homeassistant.helpers.event",
):
    sys.modules.setdefault(_mod, MagicMock())

sys.modules["homeassistant.const"].STATE_UNKNOWN = "unknown"

import custom_components.shower_guard as shower_guard
from custom_components.shower_guard.const import DOMAIN


class FakeServices:
    def __init__(self):
        self.calls = []

    async def async_call(self, domain, service, service_data=None, blocking=False):
        self.calls.append(
            {
                "domain": domain,
                "service": service,
                "service_data": service_data,
                "blocking": blocking,
            }
        )


class FakeStates:
    def __init__(self):
        self.calls = []

    def async_set(self, entity_id, new_state, attributes=None):
        self.calls.append({"entity_id": entity_id, "state": new_state, "attributes": attributes or {}})


def make_hass():
    return SimpleNamespace(data={}, services=FakeServices(), states=FakeStates())


def patch_track_state_change(monkeypatch_target=shower_guard):
    captured = {"registrations": []}

    def fake_track(hass_arg, entity_ids, callback):
        registration = {"hass": hass_arg, "entity_ids": entity_ids, "callback": callback}
        captured["registrations"].append(registration)
        captured.update(registration)
        return lambda: None

    monkeypatch_target.async_track_state_change_event = fake_track
    return captured


def callback_for(captured, entity_id):
    for registration in captured["registrations"]:
        if entity_id in registration["entity_ids"]:
            return registration["callback"]
    raise AssertionError(f"No listener registered for {entity_id}")


def test_actuator_called_on_replay_spike():
    """Set up the integration, replay the humidity spike, assert script call."""
    hass = make_hass()
    captured = patch_track_state_change()

    # Configure with scripts and presence_sensor as in user's config
    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "max_humidity_delta": 15.0,
                    "water_cut_script": "script.shower_guard_cut_water",
                    "water_available_script": "script.shower_guard_restore_water",
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")

    # Feed the sequence that starts at ~75.6 and rises to >90
    readings = [
        75.6,
        78.34,
        80.49,
        82.63,
        84.96,
        87.03,
        89.57,
        91.83,
    ]

    for h in readings:
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=str(h))})
        asyncio.run(humidity_callback(event))

    # Expect a script.turn_on call for the cut action
    assert any(
        call["domain"] == "script"
        and call["service"] == "turn_on"
        and call["service_data"] == {"entity_id": "script.shower_guard_cut_water"}
        for call in hass.services.calls
    ), f"Expected script.shower_guard_cut_water to be called, got {hass.services.calls}"


def test_actuator_calls_restore_after_humidity_falls():
    """Simulate a cut followed by humidity falling back below delta and assert restore script called."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "max_humidity_delta": 15.0,
                    "water_cut_script": "script.shower_guard_cut_water",
                    "water_available_script": "script.shower_guard_restore_water",
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")

    # Trigger cut: rapid rise
    for h in [75.6, 82.63, 87.03, 91.83]:
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=str(h))})
        asyncio.run(humidity_callback(event))

    # Now humidity falls back below the delta threshold (simulate cooling)
    for h in [93.89, 91.82, 89.5]:
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=str(h))})
        asyncio.run(humidity_callback(event))

    # Expect both cut and restore calls
    assert any(
        call["service_data"] == {"entity_id": "script.shower_guard_cut_water"}
        for call in hass.services.calls
    ), "Expected cut script to be called"

    assert any(
        call["service_data"] == {"entity_id": "script.shower_guard_restore_water"}
        for call in hass.services.calls
    ), "Expected restore script to be called"
