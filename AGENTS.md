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

Session detection logic (baseline tracking, start/end triggers, decline
detection) lives in `session.py`. Decision logic (cutoff policy) lives in
`decision.py`. **Nowhere else.**

This means:

- Never reimplement humidity thresholds, baseline tracking, cooldown timers,
  decline detection, or session state in HA YAML (automations, templates,
  scripts, or packages).
- Never reimplement decision policies (delta cutoff, presence confirmation
  gate, duration fallback) outside `decision.py`.
- Template sensors in `src/packages/` may **mirror** the component's
  published state (read an entity, format a value). They must never
  **recompute** it. As of ADR-0004, session start is no longer a static
  number a template can compare against — it's a stateful ambient-baseline
  computation only `session.py` may own. YAML should read
  `binary_sensor.shower_guard_session_active`,
  `sensor.shower_guard_humidity_delta`, and
  `sensor.shower_guard_baseline_humidity` (all published by `__init__.py`;
  see Section 4) rather than re-deriving any of them.

If you find yourself writing `{% if humidity >= 75 %}` in a Jinja template,
you are duplicating `session.py`. Stop. (There is no longer a single flat
threshold to compare against anyway — see ADR-0004.)

---

## 3. Pure Python layers must stay pure

`session.py`, `decision.py`, and `replay.py` have no Home Assistant imports.
This is intentional — it keeps them unit-testable and replayable outside HA.

- Do not add `from homeassistant...` imports to these files.
- Do not add I/O, logging side-effects, or service calls to `evaluate()` or
  `update()`. They are pure computations.
- `replay.py` must stay runnable with `python -m` and no HA environment.
- Presence (`Optional[bool]`) is consumed by **both** `session.py` (ADR-0005,
  to help end a session) and `decision.py` (ADR-0003, to gate a cutoff). Both
  take it as a plain value with no HA import — passing presence into a
  pure-layer function does not violate this section, but auditing "where
  does presence matter" now means checking both files, not one.

---

## 4. The Sensor Layer wiring (`__init__.py`)

This is the only file that bridges HA and the pure Python layers.

### How actuator, notification, and state-publishing calls work

`_call_actuator(decision)` is called by `_evaluate_and_record` on every
decision *change*. It is the one place where actuator/notification HA
service calls happen. Its structure is:

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

    # 2. Send mobile notification (WATER_CUT only, ADR-0002).
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

Separately, `_publish_decision_state(result, baseline_humidity)` runs on
**every** evaluation (not just decision changes) and calls
`hass.states.async_set()` directly — not via YAML templates — to publish:

- `sensor.shower_guard_humidity_delta` — live progress toward
  `max_humidity_delta`.
- `sensor.shower_guard_baseline_humidity` — the session's current frozen
  baseline (see ADR-0004).
- `binary_sensor.shower_guard_session_active` — whether a session is active
  (`ACTIVE` or `COOLDOWN`).

This is the **only** place these three values are computed or exposed.
Nothing else — not a template sensor, not an automation — should recompute
or re-track any of them; that would duplicate state that only
`session.py`/`decision.py` may own (ADR-0001).

### Why the notification cannot live in the HA script

The HA actuator scripts (`shower_guard_cut_water`, etc.) are static YAML.
They cannot read `configuration.yaml` at call time — they have no access to
the `notify_service` value the user configured. Hardcoding a device name
(e.g. `notify.mobile_app_your_phone`) in YAML would break on every other
installation. The `notify_service` value is only available in `__init__.py`,
where it was read from config. That is why the notification must be a second
`hass.services.async_call` inside `_call_actuator`, not an action in the
YAML script.

### Presence tracking (ADR-0003, ADR-0005)

Presence is no longer an independent cutoff trigger — it is a *confirmation
gate* on the humidity-delta cutoff (`decision.py`) and, separately, an input
that can speed up ending a session (`session.py`). The wiring layer's job is
purely to track and pass through raw values; it makes no cutoff or
end-of-session decisions itself:

- `hass.data[DOMAIN]["presence"]` holds the latest known `True`/`False`
  (`None` if unconfigured/unknown).
- `hass.data[DOMAIN]["last_presence_at"]` holds the timestamp presence was
  last seen `True`, so a brief mmWave dropout doesn't defeat a real cutoff
  within `presence_confirmation_window_seconds` (default 60s).
- On every presence change, `_handle_presence_change` **also** re-runs
  `detector.update()` against the last known humidity (mirroring what it
  already does for `_evaluate_and_record`), so a declining session can end
  the instant presence clears rather than waiting for the next humidity
  reading.

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

Published entity IDs (`ENTITY_ID_HUMIDITY_DELTA`,
`ENTITY_ID_SESSION_BASELINE_HUMIDITY`, `ENTITY_ID_SESSION_ACTIVE`) also live
here, built from `DOMAIN` — do not hardcode `sensor.shower_guard_*` /
`binary_sensor.shower_guard_*` strings elsewhere.

Note that `humidity_threshold` (a flat absolute value) no longer exists —
it was replaced by `humidity_start_delta` + `baseline_time_constant_seconds`
in ADR-0004. If you see `humidity_threshold` referenced anywhere (code,
YAML, docs, or your own assumptions), it is stale; treat it as a signal that
whatever you're looking at predates ADR-0004.

---

## 6. Tests

Every behaviour change requires a test. The test files map directly to modules:

| Module          | Test file                          |
|-----------------|-------------------------------------|
| `session.py`    | `tests/test_session.py`             |
| `decision.py`   | `tests/test_decision.py`            |
| `__init__.py`   | `tests/test_init.py` (unit-level wiring: humidity/presence callbacks, config parsing) and `tests/test_actuator_integration.py` (integration-level: actuator calls, notification, and published-state entities via `FakeStates`) |
| `replay.py`     | `tests/test_replay.py`              |

Rules:

- Tests run without a live HA instance. HA modules are stubbed at the top of
  each test file with `sys.modules.setdefault(_mod, MagicMock())`. Files that
  exercise `_publish_decision_state` also need
  `sys.modules["homeassistant.const"].STATE_UNKNOWN = "unknown"` stubbed
  before import, since that constant is read at call time.
- Use `FakeServices` (asserts `hass.services.async_call`) and `FakeStates`
  (asserts `hass.states.async_set`), both already in `test_actuator_integration.py`.
  Never mock `hass.services`/`hass.states` with a plain `MagicMock` and
  ignore call args.
- Test both the happy path and the suppression path. For any "only fires on
  change" behaviour, assert it does not fire a second time on a repeated
  identical decision.
- Test that omitting an optional config key is a strict no-op — no call, no
  warning, no crash.
- Check the current test count in `git log`/CI before starting work, and diff
  it after — do not assume a count from a prior session or from this file.

---

## 7. HA YAML files (`src/`)

The files under `src/` (packages, scripts, automations) are deployment
scaffolding. They are not the feature. Rules:

- `src/packages/shower_guard.yaml` — helpers and template sensors that
  surface component state to the HA UI. No logic. No threshold evaluation.
  Since ADR-0004, read `binary_sensor.shower_guard_session_active` rather
  than comparing a humidity entity to a fixed number — there is no fixed
  number to compare against anymore.
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

1. Read the relevant ADR(s) — check `adr/` in the current branch/checkout,
   not from memory of a previous session or an uploaded snapshot, since both
   have repeatedly turned out stale by one or more ADRs.
2. If your implementation would contradict an accepted ADR, stop and raise it
   rather than proceeding.
3. If an ADR's "What changes" table lists specific files, those are the files
   that need to change. Changing other files instead is not a valid
   implementation.

ADR-0001 through ADR-0005 are all **Accepted** and authoritative on
`origin/main`:

| ADR | Summary |
|-----|---------|
| ADR-0001 | Locked four-layer architecture; actuator abstraction via HA scripts only. |
| ADR-0002 | Mobile push notification on `WATER_CUT`, wired in `_call_actuator`. |
| ADR-0003 | Presence becomes a *confirmation gate* on the delta cutoff, not an independent trigger; duration fallback decoupled from whether `presence_sensor` is configured. |
| ADR-0004 | Session start becomes relative to a tracked ambient-baseline EMA (`humidity_start_delta`) instead of a flat `humidity_threshold`; the frozen baseline is what the Decision Engine's delta policy measures from. |
| ADR-0005 | `COOLDOWN → ENDED` gains two faster paths — decline confirmed by presence, and sustained decline alone — ahead of the existing elapsed-timer fallback. |

Don't assume this table is exhaustive going forward — newer ADRs may exist
locally or in an open PR before landing on `main`. Verify against `adr/` and
`git log origin/main` rather than trusting this list once it's more than a
glance old.

---

## 9. Checklist before opening a PR

- [ ] `session.py` and `decision.py` have no new HA imports.
- [ ] All new config keys are in `const.py` with a `CONF_` prefix.
- [ ] All external calls in `__init__.py` (scripts, notifications, and
      `hass.states.async_set()`) are wrapped in `try/except` where they can
      fail (state publishing via `async_set` is synchronous and doesn't call
      out to a service, so it doesn't need the same try/except as
      `async_call`).
- [ ] New behaviour has tests covering: fires correctly, suppressed on repeat,
      no-op when not configured.
- [ ] No session or decision logic appears in HA YAML templates — YAML reads
      published entities, it doesn't recompute them.
- [ ] `pytest tests/` passes with no failures, and the test count was
      compared against the pre-change baseline.
- [ ] The ADR "What changes" table is fully satisfied (check each row).
