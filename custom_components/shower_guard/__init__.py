# ---
# purpose: Home Assistant integration entry point for Shower Guard.
# version: 1.4.0
# note: Wires the Sensor Layer (humidity entity, optional presence entity)
#       into Session Detection and the Decision Engine, records every
#       decision into a bounded DecisionLog, and — when configured — calls
#       the actuator (an HA script) on a decision change. The Decision
#       Engine itself never references an actuator; only this wiring layer
#       does (see ADR-0001). Omitting the actuator scripts keeps that side
#       of the decision dry run (computed and logged only). Presence, when
#       configured, gates the humidity-delta cutoff rather than triggering
#       independently (see ADR-0003) — this wiring tracks the last time
#       presence was seen True so brief mmWave dropouts within the
#       confirmation window don't defeat a real cutoff. The optional
#       duration fallback (max_session_seconds) is independent of presence
#       and disabled unless explicitly configured.
# ---

import logging
from datetime import datetime

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DECISION_LOG_SIZE,
    CONF_HUMIDITY_SENSOR,
    CONF_HUMIDITY_THRESHOLD,
    CONF_MAX_HUMIDITY_DELTA,
    CONF_MAX_SESSION_SECONDS,
    CONF_NOTIFY_SERVICE,
    CONF_PRESENCE_CONFIRMATION_WINDOW_SECONDS,
    CONF_PRESENCE_SENSOR,
    CONF_WATER_AVAILABLE_SCRIPT,
    CONF_WATER_CUT_SCRIPT,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DECISION_LOG_SIZE,
    DEFAULT_HUMIDITY_THRESHOLD,
    DEFAULT_MAX_HUMIDITY_DELTA,
    DEFAULT_MAX_SESSION_SECONDS,
    DEFAULT_PRESENCE_CONFIRMATION_WINDOW_SECONDS,
    DOMAIN,
    ENTITY_ID_HUMIDITY_DELTA,
    ENTITY_ID_SESSION_BASELINE_HUMIDITY,
    VERSION,
)
from .decision import Decision, DecisionEngine, DecisionLog
from .session import SessionDetector

_LOGGER = logging.getLogger(__name__)

# States that carry no usable humidity reading.
_IGNORED_STATES = ("unknown", "unavailable", None)

# Presence sensor state -> boolean. Any other state (unknown/unavailable) is
# ignored — the last known presence value is kept.
_PRESENCE_STATE_MAP = {"on": True, "off": False}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Shower Guard integration from configuration.yaml."""
    _LOGGER.info("Shower Guard v%s initializing", VERSION)
    hass.data.setdefault(DOMAIN, {})

    domain_config = config.get(DOMAIN)
    if not domain_config:
        _LOGGER.debug(
            "No '%s' configuration found; session detection not started", DOMAIN
        )
        return True

    humidity_sensor = domain_config.get(CONF_HUMIDITY_SENSOR)
    if not humidity_sensor:
        _LOGGER.warning(
            "Shower Guard configured without '%s'; session detection disabled",
            CONF_HUMIDITY_SENSOR,
        )
        return True

    detector = SessionDetector(
        humidity_threshold=domain_config.get(
            CONF_HUMIDITY_THRESHOLD, DEFAULT_HUMIDITY_THRESHOLD
        ),
        cooldown_seconds=domain_config.get(
            CONF_COOLDOWN_SECONDS, DEFAULT_COOLDOWN_SECONDS
        ),
    )

    presence_sensor = domain_config.get(CONF_PRESENCE_SENSOR)

    # Duration fallback is independent of presence now (see ADR-0003):
    # presence confirms a delta-based cut rather than substituting for one,
    # so it no longer implies the fallback should be disabled. Off unless
    # explicitly configured, regardless of presence_sensor.
    engine = DecisionEngine(
        max_humidity_delta=domain_config.get(
            CONF_MAX_HUMIDITY_DELTA, DEFAULT_MAX_HUMIDITY_DELTA
        ),
        max_session_seconds=domain_config.get(CONF_MAX_SESSION_SECONDS, None),
        presence_confirmation_window_seconds=domain_config.get(
            CONF_PRESENCE_CONFIRMATION_WINDOW_SECONDS,
            DEFAULT_PRESENCE_CONFIRMATION_WINDOW_SECONDS,
        ),
    )
    decision_log = DecisionLog(
        max_entries=domain_config.get(
            CONF_DECISION_LOG_SIZE, DEFAULT_DECISION_LOG_SIZE
        ),
    )
    hass.data[DOMAIN]["detector"] = detector
    hass.data[DOMAIN]["decision_engine"] = engine
    hass.data[DOMAIN]["decision_log"] = decision_log
    hass.data[DOMAIN]["last_decision"] = None
    hass.data[DOMAIN]["presence"] = None  # None = no presence sensor / unknown
    hass.data[DOMAIN]["last_presence_at"] = None  # last time presence was True
    hass.data[DOMAIN]["last_humidity"] = None  # None = no humidity reading yet

    water_cut_script = domain_config.get(CONF_WATER_CUT_SCRIPT)
    water_available_script = domain_config.get(CONF_WATER_AVAILABLE_SCRIPT)
    notify_service = domain_config.get(CONF_NOTIFY_SERVICE)

    async def _call_actuator(decision: Decision) -> None:
        """Call the HA script (if configured) for the given decision. Never
        references a specific device/platform — script entities only, per
        ADR-0001. Missing script -> that side of the decision stays dry run."""
        script_entity_id = (
            water_cut_script if decision is Decision.WATER_CUT else water_available_script
        )
        if script_entity_id:
            try:
                await hass.services.async_call(
                    "script", "turn_on", {"entity_id": script_entity_id}, blocking=False
                )
                _LOGGER.info(
                    "Shower Guard: called actuator script %s for %s",
                    script_entity_id,
                    decision.value,
                )
            except Exception:  # noqa: BLE001 - HA service calls can raise various errors
                _LOGGER.exception(
                    "Shower Guard: failed to call actuator script %s for %s",
                    script_entity_id,
                    decision.value,
                )
        else:
            _LOGGER.debug(
                "Shower Guard: no actuator script configured for %s; dry run only",
                decision.value,
            )

        if decision is Decision.WATER_CUT and notify_service:
            try:
                await hass.services.async_call(
                    "notify",
                    notify_service,
                    {
                        "title": "Shower Guard",
                        "message": "Water has been cut by Shower Guard.",
                    },
                    blocking=False,
                )
                _LOGGER.info(
                    "Shower Guard: sent notification %s for water cut",
                    notify_service,
                )
            except Exception:  # noqa: BLE001 - HA service calls can raise various errors
                _LOGGER.exception(
                    "Shower Guard: failed to send notification %s for water cut",
                    notify_service,
                )

    def _publish_decision_state(result, baseline_humidity) -> None:
        """Publish the Decision Engine's current humidity delta, and the
        session's current baseline reading it's measured from, as plain HA
        states — on every evaluation (not just decision changes) — so a
        dashboard can show live progress toward max_humidity_delta. This is
        the only place that computes/exposes either value; nothing else
        should recompute or re-track them (see ADR-0001 — baseline tracking,
        including the reset on RESUMED, belongs solely to
        session.py/decision.py)."""
        delta = result.humidity_delta
        hass.states.async_set(
            ENTITY_ID_HUMIDITY_DELTA,
            f"{delta:.1f}" if delta is not None else STATE_UNKNOWN,
            {
                "unit_of_measurement": "%",
                "friendly_name": "Shower Guard Humidity Delta",
                "icon": "mdi:delta",
                "max_humidity_delta": engine.max_humidity_delta,
            },
        )
        hass.states.async_set(
            ENTITY_ID_SESSION_BASELINE_HUMIDITY,
            f"{baseline_humidity:.1f}" if baseline_humidity is not None else STATE_UNKNOWN,
            {
                "unit_of_measurement": "%",
                "friendly_name": "Shower Guard Baseline Humidity",
                "icon": "mdi:water-outline",
            },
        )

    async def _evaluate_and_record(now: datetime) -> None:
        """Evaluate the Decision Engine, record the result, publish the
        current humidity delta and baseline, and — on a decision change —
        call the configured actuator script. Shared by the humidity and
        presence callbacks so a presence change alone can trigger a
        re-evaluation and actuation."""
        result = engine.evaluate(
            detector.state,
            detector.active_since,
            now,
            humidity=hass.data[DOMAIN]["last_humidity"],
            active_since_humidity=detector.active_since_humidity,
            presence=hass.data[DOMAIN]["presence"],
            last_presence_at=hass.data[DOMAIN]["last_presence_at"],
        )
        decision_log.record(result)
        _publish_decision_state(result, detector.active_since_humidity)
        previous = hass.data[DOMAIN]["last_decision"]
        hass.data[DOMAIN]["last_decision"] = result
        if previous is None or result.decision != previous.decision:
            _LOGGER.info("Shower Guard decision: %s", result)
            await _call_actuator(result.decision)

    async def _handle_humidity_change(event) -> None:
        """Feed a new humidity reading through Session Detection and the
        Decision Engine. Every evaluation is recorded into the DecisionLog;
        decision changes call the configured actuator script."""
        new_state = event.data.get("new_state")
        if new_state is None or new_state.state in _IGNORED_STATES:
            return

        try:
            humidity = float(new_state.state)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Ignoring non-numeric humidity value from %s: %s",
                humidity_sensor,
                new_state.state,
            )
            return

        now = datetime.now()
        hass.data[DOMAIN]["last_humidity"] = humidity

        change = detector.update(humidity=humidity, now=now)
        if change is not None:
            _LOGGER.info("Shower Guard: %s", change)

        await _evaluate_and_record(now)

    hass.data[DOMAIN]["remove_listener"] = async_track_state_change_event(
        hass, [humidity_sensor], _handle_humidity_change
    )

    if presence_sensor:

        async def _handle_presence_change(event) -> None:
            """Track the latest known presence value, and — whenever it's
            True — the timestamp it was last confirmed, so a delta-triggered
            cutoff can still fire on the next humidity reading even if
            presence has since flickered off (see ADR-0003's confirmation
            window). Also immediately re-evaluates, since presence changing
            can flip a cutoff decision without waiting on humidity."""
            new_state = event.data.get("new_state")
            if new_state is None:
                return

            presence = _PRESENCE_STATE_MAP.get(new_state.state)
            if presence is None:
                return

            hass.data[DOMAIN]["presence"] = presence
            if presence is True:
                hass.data[DOMAIN]["last_presence_at"] = datetime.now()
            await _evaluate_and_record(datetime.now())

        hass.data[DOMAIN]["remove_presence_listener"] = (
            async_track_state_change_event(
                hass, [presence_sensor], _handle_presence_change
            )
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shower Guard from a config entry (UI flow — future use)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Shower Guard config entry."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
