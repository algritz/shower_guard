# Shower Guard — Backlog

Features deferred beyond the current roadmap. All milestones through v1.8
(Presence Confirmation Latch) are complete — remaining work lives here
until scheduled.

---

## Deferred Items

### YAML Config Schema Validation
- **Reason deferred:** v0.2 wiring uses lightweight manual dict parsing to avoid
  adding a schema-validation dependency before the config surface grows.
- **Target version:** Revisit once Decision Engine config (v0.4) expands options.
- **Notes:** Home Assistant ships `voluptuous`; no new dependency required when
  this is picked up.

### UI Config Flow
- **Reason deferred:** Not required for the current roadmap; YAML config is
  sufficient for v0.2/v0.3.
- **Target version:** Unscheduled — only if UI configuration becomes a goal.
- **Notes:** `async_setup_entry`/`async_unload_entry` are already scaffolded in
  `__init__.py` for this future use.

### Replay Engine — Presence Support
- **Status:** Implemented in v1.4.0 (ADR-0003). `replay()` now accepts an
  optional `presence_readings` sequence and confirms delta-triggered cuts
  the same way production wiring does — this stopped being a nice-to-have
  once presence became required to confirm any delta-based cutoff. Kept
  here as a record of the original gap.

### Nonlinear / Starting-Humidity-Dependent `max_humidity_delta`
- **Reason deferred:** RH is harder to raise further the closer it is to
  saturation (e.g. 65%→80% likely reflects more sustained steam than
  50%→60%), so a flat `max_humidity_delta` may be more forgiving to
  low-starting-baseline bathrooms than high-starting-baseline ones. As of
  v1.5 (ADR-0004), the delta policy sees the full rise from the true ambient
  baseline for the first time — making this worth revisiting — but that's
  motivated by a single logged session, not enough data to fit a formula
  without risking overfitting to one house, one season, one shower.
- **Target version:** Revisit once several real sessions (varied starting
  humidity, varied season) have been captured and run through the Replay
  Engine.
- **Notes:** Candidate approaches once data exists: a lookup table keyed by
  baseline bucket, or a cutoff expressed as a fraction of remaining headroom
  to saturation rather than a flat point value. Any change here is scoped to
  `decision.py`/`const.py` only — orthogonal to ADR-0004's session-start
  change and to ADR-0003's presence-confirmation gate, both of which stay as
  they are.

### Rolling-Slope Trend Detection for Session End
- **Reason deferred:** ADR-0005 uses a simpler peak-relative decline
  (`peak_humidity - humidity >= humidity_decline_delta`, sustained for
  `decline_confirm_seconds`) rather than a true rolling-window linear-fit
  slope. The simpler approach already fixed the logged "stuck two hours in
  Running" case; a regression-based trend detector adds real complexity
  (window sizing, minimum sample count, handling irregular push intervals)
  that isn't justified without more real sessions showing the peak-relative
  approach is too coarse.
- **Target version:** Revisit once several more real sessions are logged
  and replayed, especially any with a genuinely noisy or double-peaked
  decay curve the current approach handles poorly.
- **Notes:** Scoped to `session.py`'s `_from_cooldown` only if picked up —
  orthogonal to the dynamic baseline (ADR-0004) and presence-confirmation
  gate on the delta cutoff (ADR-0003), both of which stay as they are.

### Stuck-ACTIVE Sessions Without a Presence Sensor
- **Reason deferred:** ADR-0006 fixes the logged "session stuck ACTIVE all
  day" incident, but only for deployments with a `presence_sensor`
  configured — its fix is the presence-corroborated decline path. A
  deployment with no presence sensor (or one reading `unknown`/
  `unavailable`) whose residual post-shower humidity settles above the
  frozen `session_start_threshold` and never drops back below it can still
  get stuck `ACTIVE` indefinitely, exactly as in the original incident.
- **Target version:** Unscheduled — revisit if this is reported for a
  no-presence-sensor deployment, or proactively alongside the
  `max_active_seconds` backstop below.
- **Notes:** Deliberately not fixed by widening the no-presence
  `decline_confirmed` path to also run from `ACTIVE` — see ADR-0006's
  "Alternatives Considered" for why that's rejected as unsafe (would let
  ordinary humidity noise end a session someone is still showering in).

### `max_active_seconds` Hard Backstop
- **Reason deferred:** A duration-based cap independent of humidity and
  presence — e.g. force-end any session that's been `ACTIVE` or `COOLDOWN`
  longer than some large ceiling (an hour? two?) — would cover the
  no-presence-sensor gap above as a blunt but reliable backstop. Deferred
  rather than folded into ADR-0006 because it's an orthogonal mechanism
  (a true ceiling, not trend/presence detection) that deserves its own ADR
  and its own default-value discussion rather than being tacked onto this
  one.
- **Target version:** Unscheduled — natural pairing with the item above if
  a no-presence-sensor stuck session is ever reported.
- **Notes:** Distinct from the existing `max_session_seconds` (Decision
  Engine, water-cut fallback for no-presence deployments) — this would live
  in Session Detection and affect `binary_sensor.shower_guard_session_active`
  directly, not just the water decision.

---

## Format

Each entry should follow this structure:

```
### [Short Title]
- **Reason deferred:** Why it was not implemented now.
- **Target version:** First version where it becomes relevant.
- **Notes:** Any context to preserve for later.
```
