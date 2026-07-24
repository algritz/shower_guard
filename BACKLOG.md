# Shower Guard — Backlog

Features deferred beyond the next two active milestones.
Current active milestones: **v0.5 (Replay Support)** and **v0.6 (Presence Sensor)**.

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

---

## Format

Each entry should follow this structure:

```
### [Short Title]
- **Reason deferred:** Why it was not implemented now.
- **Target version:** First version where it becomes relevant.
- **Notes:** Any context to preserve for later.
```
