# Shower Guard — Backlog

Features deferred beyond the current roadmap. All milestones through v1.6
(Decline-Confirmed Session End) are complete — remaining work lives here
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

---

## Format

Each entry should follow this structure:

```
### [Short Title]
- **Reason deferred:** Why it was not implemented now.
- **Target version:** First version where it becomes relevant.
- **Notes:** Any context to preserve for later.
```
