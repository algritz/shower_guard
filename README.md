# Shower Guard

A reusable Home Assistant custom component that detects shower sessions using
humidity and presence, and controls water availability via actuator abstraction.

## Architecture

```
Sensor Layer → Session Detection → Decision Engine → Actuator
```

- **Sensor Layer** — Reads humidity and presence sensors from Home Assistant.
- **Session Detection** — Determines when a shower session starts and ends.
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

## Installation

1. Copy `custom_components/shower_guard/` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Configure via `configuration.yaml` (see below).

## Configuration (v1.1)

```yaml
shower_guard:
  humidity_sensor: sensor.bathroom_humidity           # required — entity providing % RH
  presence_sensor: binary_sensor.bathroom_presence    # optional — 'on'/'off' presence entity
  humidity_threshold: 75.0                            # optional — default 75.0
  cooldown_seconds: 300                               # optional — default 300 (5 min)
  max_humidity_delta: 15.0                             # optional — default 15.0 (percentage points RH)
  max_session_seconds: 900                             # optional — default 900 (15 min); ignored if presence_sensor is set
  decision_log_size: 100                               # optional — default 100 entries
  water_cut_script: script.cut_water                   # optional — called when water is cut
  water_available_script: script.restore_water         # optional — called when water is restored
```

The Sensor Layer listens for state changes on `humidity_sensor` and feeds each
reading into the Session Detection layer, then into the Decision Engine.
Session state transitions (`started`, `resumed`, `ended`) are written to the
Home Assistant log. On every decision **change**, the corresponding HA script
— `water_cut_script` or `water_available_script` — is called via the
`script.turn_on` service, per ADR-0001 (actuator abstraction via scripts only;
the Decision Engine itself never references a script or device).

**Humidity-delta cutoff (v1.1):** water is cut once humidity has risen
`max_humidity_delta` percentage points above the current baseline — not
after a fixed duration. A hot shower generates steam quickly and gets capped
sooner; a cold shower (little humidity rise) is not penalized just for
running long. The baseline is the reading at session start, and **resets on
each `RESUMED` event** (humidity rising again during the `cooldown_seconds`
window) so a sibling starting a fresh shower right after the first gets their
own baseline instead of inheriting the previous person's cumulative rise.

**Duration fallback (optional):** `max_session_seconds` (default 900s / 15
min) is only wired in when **no `presence_sensor` is configured**. With a
presence sensor, an unattended session is already caught precisely by
presence absence, and a duration cap would reintroduce the "cold shower"
stiffness the delta policy was designed to avoid — so it's disabled
automatically in that case. Without a presence sensor, it's the safety net
for a session whose humidity never rises enough to trip the delta policy.

**Actuator (v1.0):** either script may be omitted independently. If a script
for a given decision isn't configured, that side stays dry run (computed and
logged only) while the other side can still actuate. A failed script call is
caught and logged — it never crashes session tracking.

**Presence Sensor (v0.6, optional):** when `presence_sensor` is configured, an
active session with no presence detected (`'off'`) cuts water **immediately**
— taking priority over the humidity-delta policy — modeling an unattended
running shower. Presence changes are evaluated as soon as they're reported,
without waiting for the next humidity reading. If `presence_sensor` is not
configured, or its state is `unknown`/`unavailable`, behavior falls back to
the humidity-delta policy (plus the duration fallback if configured).

**Decision Logging (v0.4):** every Decision Engine evaluation — not just
changes — is recorded into a bounded, in-memory `DecisionLog`
(`decision_log_size` entries, oldest dropped first). This gives a structured
audit trail for troubleshooting and a foundation the Replay Engine (v0.5) can
build on.

## Replay Engine (v0.5)

Replay recorded or synthetic humidity readings through the **exact same**
`SessionDetector` and `DecisionEngine` classes used in production — no
decision logic is duplicated. Useful for validating threshold/delta tuning
against historical data, entirely outside Home Assistant.

```bash
python -m custom_components.shower_guard.replay readings.csv \
  --humidity-threshold 75.0 \
  --cooldown-seconds 300 \
  --max-humidity-delta 15.0 \
  --max-session-seconds 900
```

`readings.csv` must have `timestamp` (ISO 8601) and `humidity` columns. From
Python:

```python
from custom_components.shower_guard.replay import replay, load_readings_from_csv

readings = load_readings_from_csv("readings.csv")
result = replay(readings)

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
