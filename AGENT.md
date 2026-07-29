# Shower Guard — Agent Guidelines

This file tells you how to work in this codebase. Read it before touching
anything. The rules here are not style preferences — they exist to protect
an architecture that has been explicitly designed and recorded in ADRs.

---

## 1. Architecture is law

The system is a strict four-layer pipeline (ADR-0001):

```
Sensor Layer → Session Detection → Decision Engine → Actuator
```

| Layer             | File(s)         | Rule                                                        |
|-------------------|-----------------|-------------------------------------------------------------|
| Sensor Layer      | `__init__.py`   | Only layer allowed to touch HA APIs and call actuators.     |
| Session Detection | `session.py`    | Pure Python. No HA imports. No decision logic.              |
| Decision Engine   | `decision.py`   | Pure Python. No HA imports. No actuator calls. No scripts.  |
| Actuator          | HA scripts only | Called by `__init__.py` via `script.turn_on` only.          |

**If a task would cross a layer boundary, stop and re-read the relevant ADR
before continuing.**

---

## 2. Where logic lives — the most important rule

Session detection logic lives in `session.py`. Decision logic lives in
`decision.py`. **Nowhere else.**

This means:

- Never reimplement humidity thresholds, cooldown timers, or session state
  in HA YAML (automations, templates, scripts, or packages).
- Never reimplement decision policies (delta cutoff, presence check, duration
  fallback) outside `decision.py`.
- Template sensors in `src/packages/` may **mirror** the component's state
  (read an entity, format a value). They must never **recompute** it.

If you find yourself writing `{% if humidity >= 75 %}` in a Jinja template,
you are duplicating `session.py`. Stop.

---

## 3. Pure Python layers must stay pure

`session.py`, `decision.py`, and `replay.py` have no Home Assistant imports.
This is intentional — it keeps them unit-testable and replayable outside HA.

- Do not add `from homeassistant...` imports to these files.
- Do not add I/O, logging side-effects, or service calls to `evaluate()` or
  `update()`. They are pure computations.
- `replay.py` must stay runnable with `python -m` and no HA environment.

---

## 4. The Sensor Layer wiring (`__init__.py`)

This is the only file that bridges HA and the pure Python layers.

### How actuator and notification calls work

`_call_actuator(decision)` is called by `_evaluate_and_record` on every
decision *change*. It is the single place where external HA service calls
happen. Its structure is:

```python
async def _call_actuator(decision: Decision) -> None:
    # 1. Call the water control script (existing).
    script_entity_id = (
        water_cut_script if decision is Decision.WATER_CUT
        else water_available_script
    )
    if script_entity_id:
        try:
            await hass.services.async_call(
                "script", "turn_on", {"entity_id": script_entity_id},
                blocking=False
            )
        except Exception:
            _LOGGER.exception("...")

    # 2. Send mobile notification (new, WATER_CUT only).
    if decision is Decision.WATER_CUT and notify_service:
        try:
            await hass.services.async_call(
                "notify", notify_service,
                {"title": "Shower Guard", "message": "..."},
                blocking=False
            )
        except Exception:
            _LOGGER.exception("...")
```

### Why the notification cannot live in the HA script

The HA actuator scripts (`shower_guard_cut_water`, etc.) are static YAML.
They cannot read `configuration.yaml` at call time — they have no access to
the `notify_service` value the user configured. Hardcoding a device name
(e.g. `notify.mobile_app_your_phone`) in YAML would break on every other
installation. The `notify_service` value is only available in `__init__.py`,
where it was read from config. That is why the notification must be a second
`hass.services.async_call` inside `_call_actuator`, not an action in the
YAML script.

### Other rules for this file

- All external calls must be wrapped in `try/except`. Failures are logged at
  `ERROR` level and never propagate — they must not crash session tracking.
- New optional config keys follow the existing pattern: constant in
  `const.py`, read in `async_setup()`, captured in the closure.
- Never call `engine.evaluate()` or `detector.update()` from the YAML side.
---

## 5. Constants

All magic strings and default values live in `const.py`. Never scatter them
across modules. When adding a feature with a config key:

1. Add `CONF_<KEY> = "<key>"` to `const.py`.
2. Add `DEFAULT_<KEY>` if the key has a default value.
3. Read it in `async_setup()` via `domain_config.get(CONF_<KEY>, DEFAULT_<KEY>)`.

---

## 6. Tests

Every behaviour change requires a test. The test files map directly to modules:

| Module          | Test file              |
|-----------------|------------------------|
| `session.py`    | `tests/test_session.py`|
| `decision.py`   | `tests/test_decision.py`|
| `__init__.py`   | `tests/test_init.py`   |
| `replay.py`     | `tests/test_replay.py` |

Rules:

- Tests run without a live HA instance. HA modules are stubbed at the top of
  each test file with `sys.modules.setdefault(_mod, MagicMock())`.
- Use `FakeServices` (already in `test_init.py`) to assert service calls.
  Never mock `hass.services` with a plain `MagicMock` and ignore call args.
- Test both the happy path and the suppression path. For any "only fires on
  change" behaviour, assert it does not fire a second time on a repeated
  identical decision.
- Test that omitting an optional config key is a strict no-op — no call, no
  warning, no crash.

---

## 7. HA YAML files (`src/`)

The files under `src/` (packages, scripts, automations) are deployment
scaffolding. They are not the feature. Rules:

- `src/packages/shower_guard.yaml` — helpers and template sensors that
  surface component state to the HA UI. No logic. No threshold evaluation.
- `src/scripts/shower_guard.yaml` — actuator scripts called by the component.
  These own physical device control (pump, valve). Keep them thin.
- `src/automations/shower_guard.yaml` — use sparingly. Session detection is
  the component's job. An automation should react to component output, not
  reimplement it.
- `persistent_notification` is a UI-only tool. It is not a substitute for a
  mobile push notification. See ADR-0002 Alternatives Considered.

---

## 8. ADRs

Every non-trivial architectural decision has a record in `adr/`. Before
starting any task:

1. Read the relevant ADR(s).
2. If your implementation would contradict an accepted ADR, stop and raise it
   rather than proceeding.
3. If an ADR's "What changes" table lists specific files, those are the files
   that need to change. Changing other files instead is not a valid
   implementation.

ADR-0001 (locked architecture) and ADR-0002 (mobile notification) are both
**Accepted** and authoritative.

---

## 9. Checklist before opening a PR

- [ ] `session.py` and `decision.py` have no new HA imports.
- [ ] All new config keys are in `const.py` with a `CONF_` prefix.
- [ ] All external calls in `__init__.py` are wrapped in `try/except`.
- [ ] New behaviour has tests covering: fires correctly, suppressed on repeat,
      no-op when not configured.
- [ ] No session or decision logic appears in HA YAML templates.
- [ ] `pytest tests/` passes with no failures.
- [ ] The ADR "What changes" table is fully satisfied (check each row).
