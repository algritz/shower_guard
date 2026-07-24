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
  Dry run as of v0.3: decisions are computed and logged only — no actuator/script is called yet.
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
| v1.0    | Real Actuator       | 🔜 Next     |

## Installation

1. Copy `custom_components/shower_guard/` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Configure via `configuration.yaml` (see below).

## Configuration (v0.6)

```yaml
shower_guard:
  humidity_sensor: sensor.bathroom_humidity           # required — entity providing % RH
  presence_sensor: binary_sensor.bathroom_presence    # optional — 'on'/'off' presence entity
  humidity_threshold: 75.0                            # optional — default 75.0
  cooldown_seconds: 300                               # optional — default 300 (5 min)
  max_session_seconds: 900                            # optional — default 900 (15 min)
  decision_log_size: 100                              # optional — default 100 entries
```

The Sensor Layer listens for state changes on `humidity_sensor` and feeds each
reading into the Session Detection layer, then into the Decision Engine.
Session state transitions (`started`, `resumed`, `ended`) and decision changes
(`water_available`, `water_cut`) are written to the Home Assistant log.

**Presence Sensor (v0.6, optional):** when `presence_sensor` is configured, an
active session with no presence detected (`'off'`) cuts water **immediately**
— independent of `max_session_seconds` — modeling an unattended running
shower. Presence changes are evaluated as soon as they're reported, without
waiting for the next humidity reading. If `presence_sensor` is not configured,
or its state is `unknown`/`unavailable`, behavior is unchanged from v0.5
(duration-based policy only).

**Decision Logging (v0.4):** every Decision Engine evaluation — not just
changes — is recorded into a bounded, in-memory `DecisionLog`
(`decision_log_size` entries, oldest dropped first). This gives a structured
audit trail for troubleshooting and a foundation the Replay Engine (v0.5) can
build on.

**Dry run:** the Decision Engine only computes and logs decisions — it never
calls an actuator or HA script. Real actuator wiring arrives in v1.0 per
ADR-0001.

## Replay Engine (v0.5)

Replay recorded or synthetic humidity readings through the **exact same**
`SessionDetector` and `DecisionEngine` classes used in production — no
decision logic is duplicated. Useful for validating threshold/cooldown tuning
against historical data, entirely outside Home Assistant.

```bash
python -m custom_components.shower_guard.replay readings.csv \
  --humidity-threshold 75.0 \
  --cooldown-seconds 300 \
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
