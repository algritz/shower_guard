# ---
# purpose: Central constants for the Shower Guard integration.
# version: 1.4.0
# note: Add new constants here. Never scatter magic strings across modules.
# ---

DOMAIN = "shower_guard"
VERSION = "1.4.0"

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

# Optional duration-based fallback (see ADR-0001, ADR-0003). Disabled unless
# explicitly configured — independent of whether a presence sensor is set,
# since presence confirmation now gates the delta cutoff (see ADR-0003)
# rather than substituting for a duration safety net. This remains the
# fallback for a session whose humidity never rises enough to trip the delta
# policy, or that never gets a presence confirmation.
DEFAULT_MAX_SESSION_SECONDS: float = 900.0  # 15 minutes
CONF_MAX_SESSION_SECONDS = "max_session_seconds"

# Decision Logging
# Number of most recent Decision Engine evaluations to keep in memory.
DEFAULT_DECISION_LOG_SIZE: int = 100
CONF_DECISION_LOG_SIZE = "decision_log_size"

# Presence Sensor (optional Sensor Layer input, see ADR-0001, ADR-0003)
# When configured, presence acts as a *confirmation gate* on the humidity
# delta cutoff (policy 3), not an independent trigger: water is only cut once
# delta exceeds max_humidity_delta AND presence has been detected within the
# last presence_confirmation_window_seconds. Without a presence sensor
# configured, delta alone never cuts water — configure max_session_seconds
# as a fallback in that case.
CONF_PRESENCE_SENSOR = "presence_sensor"

# How recently presence must have been detected (seconds) for it to count as
# "confirmed" toward the delta cutoff gate above. A window rather than an
# instantaneous check tolerates brief mmWave dropouts mid-shower without
# treating them as absence.
DEFAULT_PRESENCE_CONFIRMATION_WINDOW_SECONDS: float = 60.0
CONF_PRESENCE_CONFIRMATION_WINDOW_SECONDS = "presence_confirmation_window_seconds"

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

# Live decision output, published as a plain HA state (see __init__.py's
# _publish_humidity_delta) — not an entity platform, consistent with this
# integration's lightweight, config-flow-free style. Exists so dashboards can
# show progress toward max_humidity_delta without duplicating the baseline
# tracking (start-of-session reading, reset on RESUMED) that only
# session.py/decision.py may own, per ADR-0001.
ENTITY_ID_HUMIDITY_DELTA = f"sensor.{DOMAIN}_humidity_delta"

# The session's current baseline reading (see
# SessionDetector.active_since_humidity) — the reference point the delta
# above is measured from. Published alongside the delta for the same reason:
# so a dashboard can show it without any YAML/template re-tracking it.
ENTITY_ID_SESSION_BASELINE_HUMIDITY = f"sensor.{DOMAIN}_baseline_humidity"
