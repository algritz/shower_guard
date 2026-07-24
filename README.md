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
| v0.5    | Replay Support      | 🔜 Next     |
| v0.6    | Presence Sensor     | Planned     |
| v1.0    | Real Actuator       | Planned     |

## Installation

1. Copy `custom_components/shower_guard/` into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.
3. Configure via `configuration.yaml` (see below).

## Configuration (v0.4)

```yaml
shower_guard:
  humidity_sensor: sensor.bathroom_humidity   # required — entity providing % RH
  humidity_threshold: 75.0                    # optional — default 75.0
  cooldown_seconds: 300                       # optional — default 300 (5 min)
  max_session_seconds: 900                    # optional — default 900 (15 min)
  decision_log_size: 100                      # optional — default 100 entries
```

The Sensor Layer listens for state changes on `humidity_sensor` and feeds each
reading into the Session Detection layer, then into the Decision Engine.
Session state transitions (`started`, `resumed`, `ended`) and decision changes
(`water_available`, `water_cut`) are written to the Home Assistant log.

**Decision Logging (v0.4):** every Decision Engine evaluation — not just
changes — is recorded into a bounded, in-memory `DecisionLog`
(`decision_log_size` entries, oldest dropped first). This gives a structured
audit trail for troubleshooting and a foundation the Replay Engine (v0.5) can
build on.

**Dry run:** the Decision Engine only computes and logs decisions — it never
calls an actuator or HA script. Real actuator wiring arrives in v1.0 per
ADR-0001.

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
