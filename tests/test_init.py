# ---
# purpose: Tests for the Sensor Layer -> Session Detection -> Decision Engine
#          -> DecisionLog wiring (v0.2/v0.3/v0.4), the optional presence
#          sensor wiring (v0.6), the actuator script wiring (v1.0), the
#          humidity-delta cutoff policy (v1.1), and the optional duration
#          fallback enabled only when no presence_sensor is configured.
# version: 1.1.0
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


class FakeServices:
    """Spy for hass.services.async_call, recording every invocation."""

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


def make_hass():
    return SimpleNamespace(data={}, services=FakeServices())


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
    # No presence_sensor -> duration fallback is enabled by default.
    assert hass.data[DOMAIN]["decision_engine"].max_session_seconds == 900.0


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


def test_async_setup_uses_custom_max_humidity_delta():
    hass = make_hass()
    patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_humidity_delta": 5.0,
                }
            },
        )
    )

    engine = hass.data[DOMAIN]["decision_engine"]
    assert engine.max_humidity_delta == 5.0


def test_async_setup_uses_custom_max_session_seconds_without_presence_sensor():
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


def test_presence_sensor_disables_duration_fallback():
    """Configuring a presence_sensor disables the duration fallback, even if
    max_session_seconds is also set — presence already handles it precisely."""
    hass = make_hass()
    patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "max_session_seconds": 60,
                }
            },
        )
    )

    engine = hass.data[DOMAIN]["decision_engine"]
    assert engine.max_session_seconds is None


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


def test_humidity_callback_records_water_cut_when_delta_exceeds_threshold():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_humidity_delta": 0,
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

    # Shower starts — no presence info yet, so the humidity-delta policy applies.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(humidity_callback(event))
    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE

    # Presence sensor reports the room is now empty — immediate cut, even
    # though the humidity delta is still small and no new humidity arrived.
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


# ---------------------------------------------------------------------------
# Actuator wiring (v1.0 — real HA script calls on a decision change)
# ---------------------------------------------------------------------------

def test_actuator_not_called_when_no_scripts_configured():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))

    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE
    assert hass.services.calls == []


def test_actuator_called_on_water_available_decision():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "water_available_script": "script.restore_water",
                }
            },
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))

    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE
    assert hass.services.calls == [
        {
            "domain": "script",
            "service": "turn_on",
            "service_data": {"entity_id": "script.restore_water"},
            "blocking": False,
        }
    ]


def test_actuator_called_on_water_cut_decision():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_humidity_delta": 0,
                    "water_cut_script": "script.cut_water",
                }
            },
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(captured["callback"](event))

    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_CUT
    assert hass.services.calls == [
        {
            "domain": "script",
            "service": "turn_on",
            "service_data": {"entity_id": "script.cut_water"},
            "blocking": False,
        }
    ]


def test_notify_service_called_on_water_cut_decision():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_humidity_delta": 0,
                    "water_cut_script": "script.cut_water",
                    "notify_service": "mobile_app_your_phone",
                }
            },
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(captured["callback"](event))

    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_CUT
    assert hass.services.calls == [
        {
            "domain": "script",
            "service": "turn_on",
            "service_data": {"entity_id": "script.cut_water"},
            "blocking": False,
        },
        {
            "domain": "notify",
            "service": "mobile_app_your_phone",
            "service_data": {
                "title": "Shower Guard",
                "message": "Water has been cut by Shower Guard.",
            },
            "blocking": False,
        },
    ]


def test_notify_service_not_called_for_water_available_decision():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "water_available_script": "script.restore_water",
                    "notify_service": "mobile_app_your_phone",
                }
            },
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))

    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE
    assert hass.services.calls == [
        {
            "domain": "script",
            "service": "turn_on",
            "service_data": {"entity_id": "script.restore_water"},
            "blocking": False,
        }
    ]


def test_actuator_not_called_again_when_decision_unchanged():
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "water_available_script": "script.restore_water",
                }
            },
        )
    )

    for _ in range(3):
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
        asyncio.run(captured["callback"](event))

    assert len(hass.services.calls) == 1


def test_actuator_only_calls_configured_side():
    """Only water_cut_script configured -> no call for WATER_AVAILABLE, but a
    call is made once the decision flips to WATER_CUT."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "water_cut_script": "script.cut_water",
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")
    presence_callback = callback_for(captured, "binary_sensor.bathroom_presence")

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(humidity_callback(event))
    assert hass.services.calls == []  # water_available_script not configured

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="off")})
    asyncio.run(presence_callback(event))

    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_CUT
    assert hass.services.calls == [
        {
            "domain": "script",
            "service": "turn_on",
            "service_data": {"entity_id": "script.cut_water"},
            "blocking": False,
        }
    ]


# ---------------------------------------------------------------------------
# Humidity delta wiring (v1.1 — the sole cutoff trigger besides presence)
# ---------------------------------------------------------------------------

def test_humidity_delta_cuts_water_quickly_via_wiring():
    """A fast humidity rise cuts water immediately through the full wiring,
    regardless of how little time has elapsed."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_humidity_delta": 5.0,
                }
            },
        )
    )

    # Session starts at the threshold (baseline = 75.0).
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="75.0")})
    asyncio.run(captured["callback"](event))
    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE

    # Humidity jumps well past the delta threshold almost immediately.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="85.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT
    assert last_decision.humidity_delta == 10.0


def test_cold_shower_stays_available_indefinitely_below_delta():
    """A low humidity-delta reading stays available well within the default
    duration fallback (900s) — addresses the 'cold shower' case."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_humidity_delta": 10.0,
                }
            },
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="75.0")})
    asyncio.run(captured["callback"](event))

    # Humidity barely rises — a cold shower generating little steam.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="77.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_AVAILABLE
    assert last_decision.humidity_delta == 2.0


def test_sibling_shower_gets_fresh_baseline_after_resume():
    """A sibling starting a shower during the cooldown window resets the
    humidity baseline, rather than inheriting the first sibling's rise."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_humidity_delta": 15.0,
                    "cooldown_seconds": 300,
                }
            },
        )
    )

    # Kid A showers: humidity rises close to (but under) the cut threshold.
    for state in ("75.0", "88.0"):
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=state)})
        asyncio.run(captured["callback"](event))
    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE

    # Kid A steps out — humidity drops, entering COOLDOWN.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="60.0")})
    asyncio.run(captured["callback"](event))
    assert hass.data[DOMAIN]["detector"].state is SessionState.COOLDOWN

    # Kid B starts within the cooldown window — RESUMED, fresh baseline.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="76.0")})
    asyncio.run(captured["callback"](event))
    assert hass.data[DOMAIN]["detector"].active_since_humidity == 76.0
    # Delta is measured from the fresh 76.0 baseline, not kid A's original 75.0.
    assert hass.data[DOMAIN]["last_decision"].humidity_delta == 0.0
    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE


# ---------------------------------------------------------------------------
# Duration fallback wiring (enabled only when no presence_sensor configured)
# ---------------------------------------------------------------------------

def test_duration_fallback_cuts_water_via_wiring_without_presence_sensor():
    """With no presence_sensor and max_session_seconds=0, the very first
    reading is cut via the duration fallback even with a tiny humidity delta."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "max_humidity_delta": 15.0,
                    "max_session_seconds": 0,
                }
            },
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="75.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT
    assert "exceeded max duration" in last_decision.reason.lower()


def test_duration_fallback_disabled_via_wiring_with_presence_sensor():
    """The same max_session_seconds=0 has no effect once a presence_sensor is
    configured — the duration fallback is disabled in favor of presence."""
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
                    "max_session_seconds": 0,
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="75.0")})
    asyncio.run(humidity_callback(event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_AVAILABLE


if __name__ == "__main__":
    tests = [
        test_async_setup_without_config_is_noop,
        test_async_setup_without_humidity_sensor_is_noop,
        test_async_setup_registers_state_listener,
        test_async_setup_uses_custom_decision_log_size,
        test_async_setup_uses_custom_max_humidity_delta,
        test_async_setup_uses_custom_max_session_seconds_without_presence_sensor,
        test_presence_sensor_disables_duration_fallback,
        test_async_setup_uses_custom_threshold_and_cooldown,
        test_humidity_callback_feeds_detector,
        test_humidity_callback_ignores_unavailable_state,
        test_humidity_callback_ignores_non_numeric_state,
        test_humidity_callback_ignores_missing_new_state,
        test_humidity_callback_records_water_available_decision,
        test_humidity_callback_records_water_cut_when_delta_exceeds_threshold,
        test_humidity_callback_records_every_evaluation_in_decision_log,
        test_async_setup_registers_presence_listener_when_configured,
        test_presence_absent_cuts_water_immediately_during_active_session,
        test_presence_callback_ignores_unknown_state,
        test_no_presence_listener_when_not_configured,
        test_actuator_not_called_when_no_scripts_configured,
        test_actuator_called_on_water_available_decision,
        test_actuator_called_on_water_cut_decision,
        test_actuator_not_called_again_when_decision_unchanged,
        test_actuator_only_calls_configured_side,
        test_humidity_delta_cuts_water_quickly_via_wiring,
        test_cold_shower_stays_available_indefinitely_below_delta,
        test_sibling_shower_gets_fresh_baseline_after_resume,
        test_duration_fallback_cuts_water_via_wiring_without_presence_sensor,
        test_duration_fallback_disabled_via_wiring_with_presence_sensor,
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
