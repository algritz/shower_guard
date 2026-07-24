# Shower Guard — Backlog

Features deferred beyond the current roadmap. All milestones through v1.1
(Humidity-Delta Cutoff) are complete — remaining work lives here until
scheduled.

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
- **Reason deferred:** v0.6 added presence as an optional input to
  `DecisionEngine.evaluate()`, but `replay.py` still only replays
  `(timestamp, humidity)` readings, to avoid changing its shipped signature.
- **Target version:** Unscheduled — pick up if replaying presence-driven
  scenarios becomes necessary for validation.
- **Notes:** Would require a parallel presence-readings sequence (or a merged
  reading type) fed into `DecisionEngine.evaluate(..., presence=...)`.

---

## Format

Each entry should follow this structure:

```
### [Short Title]
- **Reason deferred:** Why it was not implemented now.
- **Target version:** First version where it becomes relevant.
- **Notes:** Any context to preserve for later.
```
