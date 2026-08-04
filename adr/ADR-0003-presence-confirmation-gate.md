# ADR-0003 — Presence as a Confirmation Gate, Not an Independent Trigger

| Field     | Value                    |
|-----------|--------------------------|
| Status    | Accepted                 |
| Date      | 2026-08-05               |
| Author    | Technical Lead           |

---

## Context

The v0.6 presence policy cut water immediately whenever `presence=False` was
reported during an active session, modeling an "unattended running shower."
In practice this had two problems:

1. **Wrong actuator model.** The actuator here is a water supply valve/pump,
   not the showerhead. If someone finishes and physically turns off the
   shower, water has already stopped — presence going absent doesn't mean
   water is being wasted, it just means the room is empty while residual
   humidity decays. Cutting the house's water supply in that moment achieves
   nothing and can inconvenience anyone else who wants water elsewhere.
2. **False positives from delta alone.** Independently, `max_humidity_delta`
   being exceeded cut water regardless of presence. A stray humidity rise
   from another cause (weather, another steam source, a miscalibrated
   sensor) could trigger a cutoff with nobody in the bathroom at all.
3. **mmWave presence flapping.** The bathroom's mmWave sensor toggles on/off
   frequently even during genuine occupancy (observed ~60s on-bursts,
   ~285s off-gaps). A policy requiring continuous `presence=True` at the
   exact instant of evaluation would itself cause false cutoffs mid-shower.

## Decision

Presence becomes a **confirmation gate** on the humidity-delta cutoff,
rather than either substituting for it or triggering independently:

> Water is cut only when `humidity_delta >= max_humidity_delta` **and**
> presence has been detected within `presence_confirmation_window_seconds`
> (default 60s) of the evaluation — either `presence` is `True` right now,
> or it was last seen `True` at `last_presence_at` within the window.

This directly resolves problem 3: a brief mmWave dropout within the window
still counts as confirmed, so flapping doesn't defeat a real cutoff or
cause one prematurely.

The old "presence absent cuts immediately" policy is removed outright — it
is not folded into any new mechanism. If a household wants an unattended-
session safety net independent of humidity, `max_session_seconds` (see
below) is the tool for that, not presence.

### Duration fallback decoupled from presence_sensor

Previously, configuring `presence_sensor` at all forced `max_session_seconds`
to `None` (disabled), on the theory that presence already caught unattended
sessions precisely. That coupling no longer makes sense — presence is now a
gate, not a trigger, so its mere configuration says nothing about whether a
duration safety net is wanted. `max_session_seconds` is now off unless
explicitly configured, independent of `presence_sensor`.

### What changes

| File           | Change                                                                 |
|----------------|-------------------------------------------------------------------------|
| `decision.py`  | Remove the `presence is False` immediate-cut branch. Delta-exceeded now requires `presence_confirmed` (computed from `presence` and `last_presence_at`) to cut; if not confirmed, falls through to the duration fallback check instead of returning early. New `presence_confirmation_window_seconds` engine parameter. `evaluate()` gains `last_presence_at: Optional[datetime]`. |
| `const.py`     | Add `CONF_PRESENCE_CONFIRMATION_WINDOW_SECONDS` / `DEFAULT_PRESENCE_CONFIRMATION_WINDOW_SECONDS` (60.0). Duration fallback default changes from "900s if no presence_sensor" to "off unless explicitly set." |
| `__init__.py`  | Track `last_presence_at` in `hass.data[DOMAIN]`, updated whenever the presence callback observes `True`. Pass it through to `engine.evaluate()`. `max_session_seconds` no longer conditioned on `presence_sensor`. |
| `replay.py`    | New optional `presence_readings` parameter and `load_presence_readings_from_csv()`, since replay must mirror production evaluation — without presence data, replay could no longer exercise the primary cutoff path at all. Closes the previously-deferred BACKLOG item. |
| Tests          | All presence-absence-cuts and delta-alone-cuts tests rewritten around the new gate semantics, across `test_decision.py`, `test_init.py`, `test_actuator_integration.py`, `test_replay.py`. |

### Configuration

```yaml
shower_guard:
  humidity_sensor: sensor.bathroom_third_reality_inc_3rths0224z_humidity
  presence_sensor: binary_sensor.movement_detector_bathroom
  max_humidity_delta: 15.0
  presence_confirmation_window_seconds: 60   # optional, default shown
  max_session_seconds: 900                   # optional, independent safety net
```

## Consequences

- **Without a presence sensor configured, delta alone never cuts water.**
  `presence` and `last_presence_at` are always `None` in that case, so
  `presence_confirmed` is always `False`. A deployment relying solely on
  `max_humidity_delta` with no presence sensor now needs `max_session_seconds`
  as its only cutoff mechanism — this is a real behavior change for any such
  deployment, not just Shower Guard's own instance.
- Brief presence-sensor dropouts (mmWave flapping) during a real shower no
  longer cause a spurious "not confirmed" result, as long as the last `True`
  reading is within the confirmation window.
- The duration fallback is now a genuinely independent, presence-agnostic
  safety net, which also makes its behavior easier to reason about — it no
  longer silently changes based on whether an unrelated sensor is configured.
- `replay()`'s signature grows (backward compatible — new parameters are
  optional with matching defaults), but any existing caller relying on
  delta-alone cuts in a replay will see decisions change from `WATER_CUT` to
  `WATER_AVAILABLE` unless `presence_readings` is supplied.

## Alternatives Considered

- **Keep presence-absence-cuts, just make it optional/configurable** —
  Rejected. The underlying actuator-model problem (cutting a supply valve
  doesn't stop water that's already off) doesn't go away by making the
  behavior opt-in; it's simply the wrong model for this actuator.
- **Instantaneous presence check (no confirmation window)** — Rejected.
  Directly reproduces the mmWave-flapping false-cutoff risk this ADR exists
  to avoid.
- **Track presence recency in the wiring layer instead of the Decision
  Engine** — Rejected. `last_presence_at` is state, but the *decision* of
  whether it's still "confirmed" given `now` is decision logic and belongs
  in `decision.py`, consistent with ADR-0001 keeping the Decision Engine as
  the sole owner of cutoff policy. The wiring layer only tracks and passes
  through the raw timestamp.
