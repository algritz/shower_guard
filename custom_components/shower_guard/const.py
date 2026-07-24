# ---
# purpose: Central constants for the Shower Guard integration.
# version: 0.2.0
# note: Add new constants here. Never scatter magic strings across modules.
# ---

DOMAIN = "shower_guard"
VERSION = "0.2.0"

# Session Detection
# Humidity level (% RH) that triggers session start.
DEFAULT_HUMIDITY_THRESHOLD: float = 75.0

# How long (seconds) humidity must stay below the threshold before a session
# is considered ended. Prevents false endings from brief dips.
DEFAULT_COOLDOWN_SECONDS: int = 300  # 5 minutes
