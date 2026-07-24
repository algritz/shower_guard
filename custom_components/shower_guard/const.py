# ---
# purpose: Central constants for the Shower Guard integration.
# version: 0.3.0
# note: Add new constants here. Never scatter magic strings across modules.
# ---

DOMAIN = "shower_guard"
VERSION = "0.3.0"

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

# Decision Engine (dry run — no actuator call, see ADR-0001)
# Maximum time (seconds) a session may run before water is decided unavailable.
DEFAULT_MAX_SESSION_SECONDS: float = 900.0  # 15 minutes
CONF_MAX_SESSION_SECONDS = "max_session_seconds"
