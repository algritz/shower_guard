# ---
# purpose: Tests for the Sensor Layer -> Session Detection -> Decision Engine
#          -> DecisionLog wiring (v0.2/v0.3/v0.4), the actuator script wiring
#          (v1.0), the humidity-delta cutoff gated by presence confirmation
#          (v1.4, ADR-0003), the duration fallback (independent of presence,
#          off unless explicitly configured), decline/presence-confirmed
#          session end from COOLDOWN (v1.6, ADR-0005), the same
#          presence-corroborated end firing directly from ACTIVE (v1.7,
#          ADR-0006), and a temporary per-evaluation DEBUG diagnostic log
#          (v1.9.1) added to investigate a live presence-confirmation
#          discrepancy — safe to remove once resolved.
# version: 1.9.1
# ---

import asyncio
import sys
import os
from datetime import datetime
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

# STATE_UNKNOWN must be a real string (not a MagicMock attribute) since
# __init__.py compares/formats against it directly.
sys.modules["homeassistant.const"].STATE_UNKNOWN = "unknown"

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


class FakeStates:
    """Spy for hass.states.async_set, recording every invocation. Only the
    most recent state per entity_id is kept, mirroring real HA state
    storage, plus a full call history for asserting on values over time."""

    def __init__(self):
        self.calls = []
        self._current = {}

    def async_set(self, entity_id, new_state, attributes=None):
        entry = {"entity_id": entity_id, "state": new_state, "attributes": attributes or {}}
        self.calls.append(entry)
        self._current[entity_id] = entry

    def get(self, entity_id):
        return self._current.get(entity_id)


def make_hass():
    return SimpleNamespace(data={}, services=FakeServices(), states=FakeStates())


def confirm_presence(hass, at=None):
    """Directly mark presence as confirmed, for tests focused on the
    delta/actuator/notify path that don't need to simulate a full presence
    sensor wiring. Must be called after async_setup (hass.data[DOMAIN] must
    already exist)."""
    hass.data[DOMAIN]["presence"] = True
    hass.data[DOMAIN]["last_presence_at"] = at or datetime.now()


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
    # Duration fallback is off unless explicitly configured (ADR-0003) —
    # no longer tied to presence_sensor's mere presence/absence.
    assert hass.data[DOMAIN]["decision_engine"].max_session_seconds is None


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


def test_presence_sensor_no_longer_disables_duration_fallback():
    """As of ADR-0003, presence_sensor and the duration fallback are
    independent — configuring both keeps max_session_seconds active,
    unlike the old coupling where presence_sensor forced it off."""
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
    assert engine.max_session_seconds == 60


def test_async_setup_uses_custom_start_delta_and_cooldown():
    hass = make_hass()
    patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "humidity_start_delta": 5.0,
                    "cooldown_seconds": 30,
                }
            },
        )
    )

    detector = hass.data[DOMAIN]["detector"]
    assert detector.humidity_start_delta == 5.0
    assert detector.cooldown_seconds == 30


def test_async_setup_uses_custom_baseline_time_constant():
    hass = make_hass()
    patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "baseline_time_constant_seconds": 60.0,
                }
            },
        )
    )

    detector = hass.data[DOMAIN]["detector"]
    assert detector.baseline_time_constant_seconds == 60.0


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

    # First reading only seeds the ambient baseline (v1.5) — it can't start
    # a session on its own.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))
    assert detector.state is SessionState.IDLE

    # A fast jump well above the barely-moved baseline starts the session.
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


def test_humidity_callback_records_water_cut_when_delta_exceeds_threshold_with_presence():
    """Delta exceeding threshold cuts water only with presence confirmed
    (ADR-0003) — using confirm_presence() to focus on the delta/actuator
    path without re-testing presence wiring itself here."""
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
    confirm_presence(hass)

    # Seed the baseline, then a rise starts the session with a positive delta.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT


def test_evaluate_logs_diagnostic_debug_line_every_call(caplog):
    """Every evaluation — not just decision changes — logs a DEBUG line with
    the exact inputs (humidity, baseline, delta, presence, last_presence_at)
    behind that call, so a live cut/no-cut decision can be verified against
    real state rather than inferred from the sparser INFO-level change log."""
    import logging as _logging

    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    with caplog.at_level(_logging.DEBUG, logger="custom_components.shower_guard"):
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
        asyncio.run(captured["callback"](event))

    diagnostic_lines = [r for r in caplog.records if "Shower Guard eval:" in r.message]
    assert len(diagnostic_lines) == 1
    msg = diagnostic_lines[0].message
    assert "humidity=80.0" in msg
    assert "presence=None" in msg
    assert "decision=" in msg


def test_humidity_callback_does_not_cut_on_delta_alone_without_presence():
    """Exceeding max_humidity_delta with no presence data at all (no
    presence_sensor configured) does not cut water — the key behavior
    change from the old delta-alone-cuts policy (ADR-0003)."""
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

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="80.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_AVAILABLE
    assert "not cutting" in last_decision.reason.lower()


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


def test_delta_exceeded_stays_available_until_presence_confirmed():
    """Delta exceeds threshold while presence is still unconfirmed -> stays
    available. Once presence reports 'on', the same already-exceeded delta
    triggers a cut on re-evaluation without waiting for a new humidity
    reading (ADR-0003)."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "max_humidity_delta": 0,
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")
    presence_callback = callback_for(captured, "binary_sensor.bathroom_presence")

    # Seed the baseline, then a rise starts the session with an exceeded
    # delta — but presence hasn't been confirmed yet, so it stays available.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(humidity_callback(event))
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="58.0")})
    asyncio.run(humidity_callback(event))
    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE

    # Presence sensor now reports someone's there -> re-evaluation on the
    # already-exceeded delta cuts water immediately.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="on")})
    asyncio.run(presence_callback(event))

    assert hass.data[DOMAIN]["presence"] is True
    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT
    assert "presence confirmed" in last_decision.reason.lower()


def test_presence_flicker_off_within_window_still_cuts():
    """Presence flipping back to 'off' shortly after confirming doesn't
    immediately reverse an active cut — the confirmation window tolerates a
    brief mmWave dropout instead of requiring continuous detection."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "max_humidity_delta": 0,
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")
    presence_callback = callback_for(captured, "binary_sensor.bathroom_presence")

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="on")})
    asyncio.run(presence_callback(event))

    # Seed the baseline, then a rise starts the session with an exceeded
    # delta — presence is already confirmed, so it cuts right away.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(humidity_callback(event))
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="58.0")})
    asyncio.run(humidity_callback(event))
    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_CUT

    # Presence flickers off (radar dropout) -> re-evaluation still finds the
    # last confirmation within the default 60s window -> stays cut.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="off")})
    asyncio.run(presence_callback(event))

    assert hass.data[DOMAIN]["presence"] is False
    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT


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
# Session end confirmation (v1.6 — decline + presence, ADR-0005)
# ---------------------------------------------------------------------------

def test_presence_clear_ends_a_declining_session_without_a_new_humidity_reading():
    """A presence-clear event alone — no new humidity reading — can end an
    already-declining session, by re-running Session Detection against the
    last known humidity. presence_clear_confirm_seconds=0 isolates this from
    real-time timing flakiness in the test."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "presence_clear_confirm_seconds": 0,
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")
    presence_callback = callback_for(captured, "binary_sensor.bathroom_presence")

    # Settle the ambient baseline, then trigger a session with a fast rise.
    for state in ("58.0", "58.0", "61.5"):
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=state)})
        asyncio.run(humidity_callback(event))

    detector = hass.data[DOMAIN]["detector"]
    assert detector.state is SessionState.ACTIVE

    # Humidity drops back toward ambient -> COOLDOWN, with a clear decline
    # from peak (~3.5pts, above the default 1.0 humidity_decline_delta).
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="58.0")})
    asyncio.run(humidity_callback(event))
    assert detector.state is SessionState.COOLDOWN

    # No new humidity reading arrives — only presence clearing. With
    # presence_clear_confirm_seconds=0, that's enough to confirm immediately.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="off")})
    asyncio.run(presence_callback(event))

    assert detector.state is SessionState.IDLE


def test_presence_true_does_not_end_a_declining_session_via_wiring():
    """The same decline, with presence reporting True instead, does not end
    the session through this path."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "presence_clear_confirm_seconds": 0,
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")
    presence_callback = callback_for(captured, "binary_sensor.bathroom_presence")

    for state in ("58.0", "58.0", "61.5", "58.0"):
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=state)})
        asyncio.run(humidity_callback(event))

    detector = hass.data[DOMAIN]["detector"]
    assert detector.state is SessionState.COOLDOWN

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="on")})
    asyncio.run(presence_callback(event))

    assert detector.state is SessionState.COOLDOWN


# ---------------------------------------------------------------------------
# Presence-corroborated decline ends a session directly from ACTIVE
# (v1.7, ADR-0006) — end-to-end via the real wiring, for a session whose
# humidity never drops back below the frozen absolute threshold.
# ---------------------------------------------------------------------------

def test_presence_clear_ends_a_stuck_active_session_via_wiring():
    """The real incident, reproduced through async_setup's actual wiring:
    a session that rises high and declines to a plateau still above the
    frozen threshold never reaches COOLDOWN, but a presence-clear event
    (with presence_clear_confirm_seconds=0 to isolate from real-time
    timing) ends it directly from ACTIVE instead of leaving it stuck."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "humidity_start_delta": 3.0,
                    "presence_clear_confirm_seconds": 0,
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")
    presence_callback = callback_for(captured, "binary_sensor.bathroom_presence")

    # Settle the ambient baseline at 58.0, then trigger with a fast rise.
    for state in ("58.0", "58.0", "61.5"):
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=state)})
        asyncio.run(humidity_callback(event))

    detector = hass.data[DOMAIN]["detector"]
    assert detector.state is SessionState.ACTIVE

    # Peaks, then declines only to 65.0 — well above the frozen threshold
    # (~61.03), so this never crosses into COOLDOWN under the old logic.
    for state in ("90.0", "65.0"):
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=state)})
        asyncio.run(humidity_callback(event))
    assert detector.state is SessionState.ACTIVE  # confirms the pre-fix stuck state

    # Presence clears — with presence_clear_confirm_seconds=0, the decline
    # (90.0 -> 65.0, already >= the default 1.0 humidity_decline_delta) is
    # immediately corroborated, ending the session directly from ACTIVE.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="off")})
    asyncio.run(presence_callback(event))

    assert detector.state is SessionState.IDLE


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
    confirm_presence(hass)

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))
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
    confirm_presence(hass)

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))
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
    call is made once delta exceeds threshold and presence confirms it."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "presence_sensor": "binary_sensor.bathroom_presence",
                    "max_humidity_delta": 0,
                    "water_cut_script": "script.cut_water",
                }
            },
        )
    )

    humidity_callback = callback_for(captured, "sensor.bathroom_humidity")
    presence_callback = callback_for(captured, "binary_sensor.bathroom_presence")

    # Seed the baseline, then a rise starts the session with an exceeded
    # delta — but presence isn't confirmed yet, so it stays available, no
    # actuator call (water_available_script isn't configured either, so
    # this also confirms that side stays silent).
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(humidity_callback(event))
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="58.0")})
    asyncio.run(humidity_callback(event))
    assert hass.services.calls == []

    # Presence confirms -> re-evaluation cuts and calls the configured script.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="on")})
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
# Published humidity delta state (v1.2 — dashboard support)
# ---------------------------------------------------------------------------

def test_humidity_delta_published_on_every_evaluation():
    """Every evaluation — not just decision changes — publishes the current
    delta as sensor.shower_guard_humidity_delta."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    # First reading seeds the baseline (~58) — no session yet, delta unknown.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="58.0")})
    asyncio.run(captured["callback"](event))
    published = hass.states.get("sensor.shower_guard_humidity_delta")
    assert published["state"] == "unknown"

    # This rise starts the session — delta is measured from the baseline
    # (~58), not from this reading itself, so it's ~4.0 (the start delta),
    # not 0.0 as it would be under the old flat-threshold model.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="62.0")})
    asyncio.run(captured["callback"](event))
    published = hass.states.get("sensor.shower_guard_humidity_delta")
    assert published["state"] == "4.0"

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="72.0")})
    asyncio.run(captured["callback"](event))
    published = hass.states.get("sensor.shower_guard_humidity_delta")
    assert published["state"] == "14.0"
    assert published["attributes"]["unit_of_measurement"] == "%"
    assert published["attributes"]["max_humidity_delta"] == 15.0


def test_humidity_delta_published_as_unknown_when_idle():
    """No active session -> humidity_delta is None -> published as unknown,
    not 0 or blank, so a dashboard can distinguish 'no session' from
    'session just started at baseline'."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    # Low humidity -> stays IDLE -> no active_since -> humidity_delta is None.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))

    published = hass.states.get("sensor.shower_guard_humidity_delta")
    assert published["state"] == "unknown"


def test_baseline_humidity_published_on_session_start():
    """Session start publishes the tracked ambient baseline, NOT the raw
    reading that crossed the start trigger (v1.5, ADR-0004) — this is the
    actual fix for the bug where a session starting from a high ambient
    baseline hid its pre-trigger rise from the delta-cutoff policy."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="58.0")})
    asyncio.run(captured["callback"](event))
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="62.0")})
    asyncio.run(captured["callback"](event))

    published = hass.states.get("sensor.shower_guard_baseline_humidity")
    assert published["state"] == "58.0"  # the ambient baseline, not "62.0"
    assert published["attributes"]["unit_of_measurement"] == "%"


def test_baseline_humidity_published_as_unknown_when_idle():
    """No active session -> baseline is None -> published as unknown."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass, {DOMAIN: {"humidity_sensor": "sensor.bathroom_humidity"}}
        )
    )

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="50.0")})
    asyncio.run(captured["callback"](event))

    published = hass.states.get("sensor.shower_guard_baseline_humidity")
    assert published["state"] == "unknown"


def test_baseline_humidity_resets_on_sibling_resume():
    """Baseline published entity reflects the RESUMED reading (fresh
    baseline for a sibling shower), not the original STARTED reading —
    mirrors detector.active_since_humidity's own reset behavior."""
    hass = make_hass()
    captured = patch_track_state_change()

    asyncio.run(
        shower_guard.async_setup(
            hass,
            {
                DOMAIN: {
                    "humidity_sensor": "sensor.bathroom_humidity",
                    "cooldown_seconds": 300,
                }
            },
        )
    )

    # seed, start, cooldown, resume (sibling) — one extra seed reading vs.
    # the old flat-threshold model, since the first reading here only seeds
    # the baseline rather than starting a session immediately.
    for state in ("70.0", "76.0", "60.0", "76.0"):
        event = SimpleNamespace(data={"new_state": SimpleNamespace(state=state)})
        asyncio.run(captured["callback"](event))

    published = hass.states.get("sensor.shower_guard_baseline_humidity")
    assert published["state"] == "76.0"


# ---------------------------------------------------------------------------
# Humidity delta wiring (v1.1 — the sole cutoff trigger besides presence)
# ---------------------------------------------------------------------------

def test_humidity_delta_cuts_water_quickly_via_wiring():
    """A fast humidity rise cuts water immediately through the full wiring,
    regardless of how little time has elapsed, given presence is confirmed."""
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
    confirm_presence(hass)

    # First reading only seeds the baseline (~75.0) — no session yet, so
    # this evaluation is trivially available (no active session).
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="75.0")})
    asyncio.run(captured["callback"](event))
    assert hass.data[DOMAIN]["last_decision"].decision is Decision.WATER_AVAILABLE

    # Humidity jumps well past the delta threshold almost immediately —
    # this reading both starts the session and exceeds the cutoff delta.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="85.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT
    # The baseline drifts by a negligible amount between the two readings
    # (real wall-clock microseconds against a 600s EMA time constant), so
    # compare with a small tolerance rather than exact equality.
    assert abs(last_decision.humidity_delta - 10.0) < 0.01


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

    # Seed the baseline, then a rise that clears the start delta (so the
    # session actually starts) but stays well under the cutoff delta.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="70.0")})
    asyncio.run(captured["callback"](event))
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="74.0")})
    asyncio.run(captured["callback"](event))

    # Humidity barely rises further — a cold shower generating little steam.
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="77.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_AVAILABLE
    assert abs(last_decision.humidity_delta - 7.0) < 0.01


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

    # Seed the baseline near 72, then kid A showers: humidity rises close to
    # (but under) the cut threshold (delta ~14 < 15).
    for state in ("72.0", "86.0"):
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

    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="70.0")})
    asyncio.run(captured["callback"](event))
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="75.0")})
    asyncio.run(captured["callback"](event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT
    assert "exceeded max duration" in last_decision.reason.lower()


def test_duration_fallback_still_active_via_wiring_with_presence_sensor():
    """As of ADR-0003, max_session_seconds is independent of presence_sensor
    — with both configured, the duration fallback still fires, unlike the
    old coupling where presence_sensor forced it off."""
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
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="70.0")})
    asyncio.run(humidity_callback(event))
    event = SimpleNamespace(data={"new_state": SimpleNamespace(state="75.0")})
    asyncio.run(humidity_callback(event))

    last_decision = hass.data[DOMAIN]["last_decision"]
    assert last_decision.decision is Decision.WATER_CUT
    assert "exceeded max duration" in last_decision.reason.lower()


if __name__ == "__main__":
    tests = [
        test_async_setup_without_config_is_noop,
        test_async_setup_without_humidity_sensor_is_noop,
        test_async_setup_registers_state_listener,
        test_async_setup_uses_custom_decision_log_size,
        test_async_setup_uses_custom_max_humidity_delta,
        test_async_setup_uses_custom_max_session_seconds_without_presence_sensor,
        test_presence_sensor_no_longer_disables_duration_fallback,
        test_async_setup_uses_custom_start_delta_and_cooldown,
        test_async_setup_uses_custom_baseline_time_constant,
        test_humidity_callback_feeds_detector,
        test_humidity_callback_ignores_unavailable_state,
        test_humidity_callback_ignores_non_numeric_state,
        test_humidity_callback_ignores_missing_new_state,
        test_humidity_callback_records_water_available_decision,
        test_humidity_callback_records_water_cut_when_delta_exceeds_threshold_with_presence,
        test_evaluate_logs_diagnostic_debug_line_every_call,
        test_humidity_callback_does_not_cut_on_delta_alone_without_presence,
        test_humidity_callback_records_every_evaluation_in_decision_log,
        test_async_setup_registers_presence_listener_when_configured,
        test_delta_exceeded_stays_available_until_presence_confirmed,
        test_presence_flicker_off_within_window_still_cuts,
        test_presence_callback_ignores_unknown_state,
        test_no_presence_listener_when_not_configured,
        test_presence_clear_ends_a_declining_session_without_a_new_humidity_reading,
        test_presence_true_does_not_end_a_declining_session_via_wiring,
        test_presence_clear_ends_a_stuck_active_session_via_wiring,
        test_actuator_not_called_when_no_scripts_configured,
        test_actuator_called_on_water_available_decision,
        test_actuator_called_on_water_cut_decision,
        test_actuator_not_called_again_when_decision_unchanged,
        test_actuator_only_calls_configured_side,
        test_humidity_delta_cuts_water_quickly_via_wiring,
        test_cold_shower_stays_available_indefinitely_below_delta,
        test_sibling_shower_gets_fresh_baseline_after_resume,
        test_duration_fallback_cuts_water_via_wiring_without_presence_sensor,
        test_duration_fallback_still_active_via_wiring_with_presence_sensor,
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
