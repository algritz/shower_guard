# ---
# purpose: Home Assistant integration entry point for Shower Guard.
# version: 0.4.0
# note: Wires the Sensor Layer (humidity entity) into Session Detection and
#       the Decision Engine, and records every decision into a bounded
#       DecisionLog (v0.4). Still dry run — no actuator or HA script is
#       invoked yet (see ADR-0001, roadmap v1.0).
# ---

import logging
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_COOLDOWN_SECONDS,
    CONF_DECISION_LOG_SIZE,
    CONF_HUMIDITY_SENSOR,
    CONF_HUMIDITY_THRESHOLD,
    CONF_MAX_SESSION_SECONDS,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DECISION_LOG_SIZE,
    DEFAULT_HUMIDITY_THRESHOLD,
    DEFAULT_MAX_SESSION_SECONDS,
    DOMAIN,
    VERSION,
)
from .decision import DecisionEngine, DecisionLog
from .session import SessionDetector

_LOGGER = logging.getLogger(__name__)

# States that carry no usable humidity reading.
_IGNORED_STATES = ("unknown", "unavailable", None)


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
    engine = DecisionEngine(
        max_session_seconds=domain_config.get(
            CONF_MAX_SESSION_SECONDS, DEFAULT_MAX_SESSION_SECONDS
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

    async def _handle_humidity_change(event) -> None:
        """Feed a new humidity reading through Session Detection and the
        Decision Engine (dry run — logged only, no actuator call). Every
        evaluation is recorded into the DecisionLog."""
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

        change = detector.update(humidity=humidity, now=now)
        if change is not None:
            _LOGGER.info("Shower Guard: %s", change)

        result = engine.evaluate(detector.state, detector.active_since, now)
        decision_log.record(result)
        previous = hass.data[DOMAIN]["last_decision"]
        hass.data[DOMAIN]["last_decision"] = result
        if previous is None or result.decision != previous.decision:
            _LOGGER.info("Shower Guard decision (dry run): %s", result)

    hass.data[DOMAIN]["remove_listener"] = async_track_state_change_event(
        hass, [humidity_sensor], _handle_humidity_change
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
