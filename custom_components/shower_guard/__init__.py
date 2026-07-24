# ---
# purpose: Home Assistant integration entry point for Shower Guard.
# version: 0.1.0
# note: Setup and teardown hooks for the HA integration lifecycle.
# ---

import logging

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Shower Guard integration from configuration.yaml."""
    _LOGGER.info("Shower Guard v%s initializing", VERSION)
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Shower Guard from a config entry (UI flow — future use)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a Shower Guard config entry."""
    hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return True
