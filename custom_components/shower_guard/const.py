# ---
# purpose: Central constants for the Shower Guard integration.
# version: 1.1.0
# note: Add new constants here. Never scatter magic strings across modules.
# ---

DOMAIN = "shower_guard"
VERSION = "1.1.0"

# Session Detection
# Humidity level (% RH) that triggers session start.
DEFAULT_HUMIDITY_THRESHOLD: float = 75.0

# How long (seconds) humidity must stay below the threshold before a session
# is considered ended. Prevents false endings from brief dips.
DEFAULT_COOLDOWN_SECONDS: int = 300  # 5 minutes

# configuration.yaml keys (Sensor Layer wiring)
CONF_HUMIDITY_SENSOR = "humidity_sensor"
CONF_HUMIDITY_THRESHOLD = "humidity_threshold"
CONF_COOLDOWN_SECONDS = "cooldown_seconds"

# Decision Engine (see ADR-0001)
# Maximum humidity rise (percentage points RH) above the current baseline
# (session start; reset on RESUMED so a sibling starting a fresh shower
# during the cooldown window isn't penalized by the previous person's
# cumulative rise) before water is decided unavailable.
DEFAULT_MAX_HUMIDITY_DELTA: float = 15.0
CONF_MAX_HUMIDITY_DELTA = "max_humidity_delta"

# Optional duration-based fallback (see ADR-0001). Only wired in when no
# presence_sensor is configured — with a presence sensor, an unattended
# session is already caught precisely; without one, this is the safety net
# for a session whose humidity never rises enough to trip the delta policy.
DEFAULT_MAX_SESSION_SECONDS: float = 900.0  # 15 minutes
CONF_MAX_SESSION_SECONDS = "max_session_seconds"

# Decision Logging
# Number of most recent Decision Engine evaluations to keep in memory.
DEFAULT_DECISION_LOG_SIZE: int = 100
CONF_DECISION_LOG_SIZE = "decision_log_size"

# Presence Sensor (optional Sensor Layer input, see ADR-0001)
# When configured, an active session with no presence detected cuts water
# immediately, regardless of humidity delta.
CONF_PRESENCE_SENSOR = "presence_sensor"

# Actuator (v1.0, see ADR-0001)
# HA script entities called on a decision change. The Decision Engine never
# references these directly — only the Sensor Layer wiring (__init__.py)
# calls them. Either/both may be omitted, in which case that side of the
# decision remains dry run (computed and logged only).
CONF_WATER_CUT_SCRIPT = "water_cut_script"
CONF_WATER_AVAILABLE_SCRIPT = "water_available_script"

# Optional mobile notification on water cut (ADR-0002).
# Must be a Home Assistant notify service name, e.g. mobile_app_your_phone.
CONF_NOTIFY_SERVICE = "notify_service"
