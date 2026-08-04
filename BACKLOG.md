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
- **Status:** Implemented in v1.4.0 (ADR-0003). `replay()` now accepts an
  optional `presence_readings` sequence and confirms delta-triggered cuts
  the same way production wiring does — this stopped being a nice-to-have
  once presence became required to confirm any delta-based cutoff. Kept
  here as a record of the original gap.

---

## Format

Each entry should follow this structure:

```
### [Short Title]
- **Reason deferred:** Why it was not implemented now.
- **Target version:** First version where it becomes relevant.
- **Notes:** Any context to preserve for later.
```
