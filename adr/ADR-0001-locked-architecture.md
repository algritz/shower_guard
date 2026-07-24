# ADR-0001 — Locked Architecture: Sensor → Session Detection → Decision Engine → Actuator

| Field     | Value                    |
|-----------|--------------------------|
| Status    | Accepted                 |
| Date      | 2026-07-24               |
| Author    | Technical Lead           |

---

## Context

Shower Guard must work across two physical deployment targets:
1. **Apartment** — A smart plug controlling a water pump.
2. **House** — A Wi-Fi smart valve controlling the main water supply.

The core logic (session detection and water availability decisions) must not change
depending on which actuator is installed. Additionally, the project scope must stay
focused: it is a shower session detector and water gating system, not a general-purpose
home automation framework.

## Decision

The system is structured as a strict four-layer pipeline:

```
Sensor Layer → Session Detection → Decision Engine → Actuator
```

### Layer Responsibilities

| Layer             | Responsibility                                                    |
|-------------------|-------------------------------------------------------------------|
| Sensor Layer      | Reads raw humidity and presence values from Home Assistant.       |
| Session Detection | Determines session start/end from sensor data.                    |
| Decision Engine   | Decides if water should remain available. Actuator-agnostic.      |
| Actuator          | Executes the water control action. Abstracted via HA scripts.     |

### Actuator Abstraction

The Decision Engine communicates via **Home Assistant scripts only**. It never
references a specific device, entity platform, or hardware directly. This ensures
the same decision logic runs unchanged on both deployment targets.

## Consequences

- The Decision Engine cannot make decisions based on which actuator is active.
- Adding a new deployment target only requires a new HA script implementation.
- Any change that violates this layer separation requires a new ADR to supersede this one.
- If this boundary is accidentally crossed during implementation, stop, reference this
  ADR, and correct course before continuing.

## Alternatives Considered

- **Single monolithic automation** — Rejected. Couples logic to hardware; not reusable.
- **MQTT event bus** — Rejected. Out of scope; adds infrastructure complexity with no
  benefit at current scale.
