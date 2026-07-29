# ADR-0002 — Mobile Notification on Water Cut Decision

| Field     | Value                    |
|-----------|--------------------------|
| Status    | Accepted                 |
| Date      | 2026-07-28               |
| Author    | Technical Lead           |

---

## Context

Once the Decision Engine decides water should be cut, the only feedback visible
to the household is the physical actuator (pump off / valve closed). There is no
out-of-band signal to the person responsible for the shower, which makes it hard
to know the system acted without being in the bathroom or checking the HA log.

A mobile push notification on every `WATER_CUT` decision change would close
this gap without adding hardware or a new integration dependency — Home Assistant
already exposes a `notify` domain, and the HA Companion app registers a
per-device service (e.g. `mobile_app_your_phone`) that maps directly onto it.

## Decision

A new optional config key, `notify_service`, is added to the wiring layer
(`__init__.py`) and surfaced in `const.py` as `CONF_NOTIFY_SERVICE`.

When configured, `_call_actuator` sends a `notify.<service>` call to Home
Assistant immediately after (and only after) a decision changes to `WATER_CUT`.
It does **not** fire on `WATER_AVAILABLE`, and it does **not** fire on repeated
evaluations that produce the same decision — matching the existing actuator
suppression logic.

The notification is wired exclusively in `_call_actuator` inside the Sensor
Layer (`__init__.py`). `DecisionEngine` and `SessionDetector` are not modified.

### What changes

| File          | Change                                                                 |
|---------------|------------------------------------------------------------------------|
| `const.py`    | Add `CONF_NOTIFY_SERVICE = "notify_service"`.                          |
| `__init__.py` | Read `notify_service` from config; call `hass.services.async_call("notify", notify_service, …)` inside `_call_actuator` on `WATER_CUT`. Errors are caught and logged — they never crash session tracking. |
| `README.md`   | Document the new optional config key.                                  |
| `test_init.py`| Add tests asserting the notify call fires on `WATER_CUT` and is suppressed on repeated identical decisions and on `WATER_AVAILABLE`. |

### Configuration

```yaml
shower_guard:
  humidity_sensor: sensor.bathroom_humidity
  water_cut_script: script.cut_water
  notify_service: mobile_app_your_phone   # notify.<service> target
```

`notify_service` is optional. Omitting it preserves existing behaviour
exactly — no notification is sent and no warning is logged.

## Consequences

- Households get an immediate out-of-band signal when the system cuts water,
  without needing to check the HA log or be physically present.
- The `DecisionEngine` and `SessionDetector` layers are unchanged; ADR-0001's
  layer separation is fully respected.
- Notification failure is non-fatal: a caught exception is logged at
  `ERROR` level, and session tracking continues normally — matching the
  existing pattern for actuator script failures.
- The `notify` domain is built into Home Assistant; no new dependency is
  introduced. The specific service name (`mobile_app_*`) is registered
  automatically by the HA Companion app on the user's device.
- If `notify_service` is omitted, the change is a pure no-op for existing
  deployments.

## Alternatives Considered

- **Notify on every evaluation** — Rejected. Would produce a notification on
  every humidity reading during a cut session, creating noise.
- **Notify on `WATER_AVAILABLE` as well** — Deferred. Not requested; can be
  added as a second optional key (`notify_service_available`) if needed.
- **Persistent HA notification (via `persistent_notification`)** — Rejected.
  Requires no config but is only visible inside the HA UI, not on a phone.
- **New `Notifier` layer in the pipeline** — Rejected. Notification is
  observability, not actuation. Elevating it to a pipeline layer would violate
  the scope constraint in ADR-0001 and add abstraction with no benefit at
  current scale.
