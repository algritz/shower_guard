# Shower Guard

A reusable Home Assistant custom component that detects shower sessions using
humidity and presence, and controls water availability via actuator abstraction.

## Architecture

```
Sensor Layer → Session Detection → Decision Engine → Actuator
```

- **Sensor Layer** — Reads humidity and presence sensors from Home Assistant.
- **Session Detection** — Determines when a shower session starts and ends,
  using a dynamic ambient-humidity baseline rather than a flat threshold
  (v1.5, ADR-0004).
- **Decision Engine** — Decides whether water should remain available. Actuator-agnostic.
- **Actuator** — Abstracted via HA scripts. Two deployment targets:
  - Apartment: smart plug controlling a pump.
  - House: Wi-Fi smart valve controlling the water supply.

## Roadmap

| Version | Feature             | Status      |
|---------|---------------------|-------------|
| v0.1    | Project Scaffold    | ✅ Done     |
| v0.2    | Session Detection   | ✅ Done     |
| v0.3    | Dry Run             | ✅ Done     |
| v0.4    | Decision Logging    | ✅ Done     |
| v0.5    | Replay Support      | ✅ Done     |
| v0.6    | Presence Sensor     | ✅ Done     |
| v1.0    | Real Actuator       | ✅ Done     |
| v1.1    | Humidity-Delta Cutoff | ✅ Done   |
| v1.2    | Live Humidity Delta Entity | ✅ Done |
| v1.3    | Live Baseline Humidity Entity | ✅ Done |
| v1.4    | Presence Confirmation Gate (ADR-0003) | ✅ Done |
| v1.5    | Dynamic Baseline Session Start (ADR-0004) | ✅ Done |
| v1.6    | Decline-Confirmed Session End (ADR-0005) | ✅ Done |
| v1.7    | Presence-Corroborated End From ACTIVE (ADR-0006) | ✅ Done |
| v1.8    | Presence Confirmation Latch (ADR-0007) | ✅ Done |

## Installation

1. Copy `custom_components/shower_guard/` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Configure via `configuration.yaml` (see below).

## Configuration (v1.8)

```yaml
shower_guard:
  humidity_sensor: sensor.bathroom_humidity           # required — entity providing % RH
  presence_sensor: binary_sensor.bathroom_presence    # optional — 'on'/'off' presence entity
  humidity_start_delta: 3.0                           # optional — default 3.0 (points above ambient baseline that trigger a session)
  baseline_time_constant_seconds: 600                 # optional — default 600 (10 min EMA time constant for the ambient baseline)
  cooldown_seconds: 300                               # optional — default 300 (5 min; stable/flat-humidity fallback, see ADR-0005)
  humidity_decline_delta: 1.0                          # optional — default 1.0 (points below session peak that count as "declining", ADR-0005)
  decline_confirm_seconds: 60                          # optional — default 60s (sustained decline required with no presence data, ADR-0005)
  presence_clear_confirm_seconds: 60                   # optional — default 60s (sustained presence-clear that fast-ends a declining session, ADR-0005)
  max_humidity_delta: 15.0                             # optional — default 15.0 (percentage points RH)
  presence_confirmation_window_seconds: 60             # optional — default 60s (see ADR-0003)
  max_session_seconds: 900                             # optional — off by default; independent of presence_sensor
  decision_log_size: 100                               # optional — default 100 entries
  water_cut_script: script.cut_water                   # optional — called when water is cut
  water_available_script: script.restore_water         # optional — called when water is restored
  notify_service: mobile_app_your_phone                 # optional — notify.<service> target for WATER_CUT only
```

The Sensor Layer listens for state changes on `humidity_sensor` and feeds each
reading into the Session Detection layer, then into the Decision Engine.
Session state transitions (`started`, `resumed`, `ended`) are written to the
Home Assistant log. On every decision **change**, the corresponding HA script
— `water_cut_script` or `water_available_script` — is called via the
`script.turn_on` service, per ADR-0001 (actuator abstraction via scripts only;
the Decision Engine itself never references a script or device).

**Dynamic baseline session start (v1.5, ADR-0004):** a session starts once
humidity has risen `humidity_start_delta` points above a continuously-tracked
ambient baseline — not once it crosses a fixed absolute floor. The baseline
is a time-based EMA (smoothed over `baseline_time_constant_seconds`) tracked
only while idle, and is frozen the instant a session starts. This replaces
the old flat `humidity_threshold`: a bathroom's ambient humidity varies by
season and ventilation, and a flat floor either under-detects (baseline high,
floor crossed late — hiding real pre-crossing rise from the delta-cutoff
policy below) or over-detects (baseline low, floor crossed on a minor
fluctuation) depending on the day. See ADR-0004 for the full rationale,
including the real-world case that motivated it. Note: a session's
very-first-ever humidity reading only seeds the baseline — it can never
itself trigger a start.

**Humidity-delta cutoff (v1.1, gated by presence as of v1.4 — see ADR-0003):**
water is cut once humidity has risen `max_humidity_delta` percentage points
above the session-start baseline **and** presence has been confirmed within
`presence_confirmation_window_seconds` — not on delta alone. As of v1.5, that
baseline is the true ambient reading tracked before the session started (see
above), not the raw reading that happened to cross the old flat threshold —
so this policy now sees the session's full rise. The baseline **resets on
each `RESUMED` event** (humidity rising again during the `cooldown_seconds`
window) so a sibling starting a fresh shower right after the first gets their
own baseline instead of inheriting the previous person's cumulative rise.
Without a `presence_sensor` configured at all, delta alone never cuts water —
see Presence Sensor below.

**Duration fallback (optional, independent of presence — ADR-0003):**
`max_session_seconds` is off unless explicitly configured, regardless of
whether `presence_sensor` is set. It's the safety net for a session that
never gets a delta+presence match — no presence sensor, a cold shower that
never trips the delta, or a delta that trips but is never presence-confirmed.

**Actuator (v1.0):** either script may be omitted independently. If a script
for a given decision isn't configured, that side stays dry run (computed and
logged only) while the other side can still actuate. A failed script call is
caught and logged — it never crashes session tracking.

**Presence Sensor (v0.6; behavior changed in v1.4 — see ADR-0003):** when
`presence_sensor` is configured, presence acts as a **confirmation gate** on
the humidity-delta cutoff, not an independent trigger. Water is cut only when
delta exceeds `max_humidity_delta` **and** presence has been detected within
the last `presence_confirmation_window_seconds` (default 60s) — either
presence is `True` right now, or it was last seen `True` within that window.
This tolerates brief presence-sensor dropouts (e.g. mmWave flapping) mid-shower
without either causing a false cutoff or missing a real one. Presence going
absent on its own no longer cuts water immediately — that policy was removed
in v1.4 as the wrong model for a supply-valve actuator (see ADR-0003 for the
full rationale). If `presence_sensor` is not configured, or its state is
`unknown`/`unavailable`, the delta policy never confirms — configure
`max_session_seconds` as a fallback in that case.

**Decline-confirmed session end (v1.6, ADR-0005):** once a session enters the
post-threshold cooldown window, it now ends on whichever of these fires
first, instead of purely waiting out `cooldown_seconds`:

1. Humidity has fallen `humidity_decline_delta` points from the session's
   peak **and** presence has read continuously `False` for
   `presence_clear_confirm_seconds` — the fastest path, when a presence
   sensor is configured.
2. The same decline has held continuously for `decline_confirm_seconds`,
   with no presence data required.
3. `cooldown_seconds` has elapsed with neither of the above true — the
   original "stable/flat humidity" fallback, unchanged.

This fixes a case where a session's binary sensor stayed `on` for over two
hours during a long, slow humidity decay: ADR-0004's lower, ambient-relative
threshold made it easy for a shallow decay tail to keep re-crossing the
threshold and resetting the cooldown timer. See ADR-0005 for the full
rationale.

**Presence-corroborated end from ACTIVE (v1.7, ADR-0006):** path 1 above
(decline + confirmed presence-clear) is now also checked continuously while
a session is still `ACTIVE` — not only after it reaches the post-threshold
cooldown window. This fixes a related case ADR-0005 didn't cover: if
post-shower residual humidity settles above the frozen `session_start_
threshold` (e.g. a tight `humidity_start_delta`, or a bathroom that
naturally holds humidity for hours) and never drops back below it, the
session never reaches `COOLDOWN` at all, so none of ADR-0005's end-of-
session checks ever run — leaving the session `ACTIVE` indefinitely. With a
presence sensor, this is now caught directly: a confirmed decline from peak
plus continuously-clear presence ends the session immediately regardless of
the absolute humidity level. **Without a presence sensor, this specific
failure mode is not fixed** — path 2 (decline alone) remains deliberately
restricted to `COOLDOWN`, since without presence corroboration a session
still `ACTIVE` must not end just because humidity dipped momentarily. See
ADR-0006 for the full rationale, including the incident that surfaced this.

**Presence confirmation latch (v1.8, ADR-0007):** the humidity-delta cutoff's
presence confirmation (ADR-0003) now latches per delta baseline instead of
being re-checked fresh on every evaluation. Once presence has been confirmed
at least once for the current baseline, a later momentary presence-sensor
gap (e.g. an mmWave dropout during real, continuous occupancy) cannot alone
flip an already-cut decision back to available while the delta remains
exceeded. This fixes a logged case where a flaky presence sensor toggled the
water cut on and off roughly six times in 13 minutes during one continuous,
attended shower. The latch resets on a fresh baseline (a new `STARTED` or a
sibling's `RESUMED`) or when the session ends, and never overrides a genuine
humidity decline — it only matters while delta is still over threshold. See
ADR-0007 for the full rationale.

**Decision Logging (v0.4):** every Decision Engine evaluation — not just
changes — is recorded into a bounded, in-memory `DecisionLog`
(`decision_log_size` entries, oldest dropped first). This gives a structured
audit trail for troubleshooting and a foundation the Replay Engine (v0.5) can
build on.

## Replay Engine (v0.5; presence support added v1.4 — ADR-0003; start-delta
## params updated v1.5 — ADR-0004)

Replay recorded or synthetic humidity (and optionally presence) readings
through the **exact same** `SessionDetector` and `DecisionEngine` classes
used in production — no decision logic is duplicated. Useful for validating
start-delta/delta/presence-window tuning against historical data, entirely
outside Home Assistant.

```bash
python -m custom_components.shower_guard.replay readings.csv \
  --presence-csv presence.csv \
  --humidity-start-delta 3.0 \
  --baseline-time-constant-seconds 600 \
  --cooldown-seconds 300 \
  --humidity-decline-delta 1.0 \
  --decline-confirm-seconds 60 \
  --presence-clear-confirm-seconds 60 \
  --max-humidity-delta 15.0 \
  --presence-confirmation-window-seconds 60 \
  --max-session-seconds 900
```

`readings.csv` must have `timestamp` (ISO 8601) and `humidity` columns.
`presence.csv` (optional — since v1.4, per ADR-0003, delta-triggered cuts
won't confirm without it) must have `timestamp` and `presence` columns
(`on`/`off` or `true`/`false`). From Python:

```python
from custom_components.shower_guard.replay import (
    replay, load_readings_from_csv, load_presence_readings_from_csv,
)

readings = load_readings_from_csv("readings.csv")
presence_readings = load_presence_readings_from_csv("presence.csv")
result = replay(readings, presence_readings=presence_readings)

result.state_changes   # list[StateChange]
result.decision_log     # DecisionLog — same object used by the live integration
```

## Architecture Decision Records

All architectural decisions are documented in [`adr/`](adr/). Accepted ADRs are
authoritative until explicitly superseded.

## Development

```bash
# Run tests
pytest tests/
```

## Non-Goals

- Does not solve every home automation problem.
- Does not redesign accepted architecture unless explicitly requested.
