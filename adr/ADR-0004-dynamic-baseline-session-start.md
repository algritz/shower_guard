# ADR-0004 — Dynamic Ambient Baseline Replaces the Flat Session-Start Threshold

| Field     | Value                    |
|-----------|--------------------------|
| Status    | Accepted                 |
| Date      | 2026-08-06               |
| Author    | Technical Lead           |

---

## Context

Since v0.2, `SessionDetector` has used a single flat absolute value,
`humidity_threshold` (default 75.0% RH): a session started the instant a
reading crossed that floor. ADR-0003 changed *what* it takes to cut water
during a session (presence must confirm a delta-exceeded cutoff), but never
touched *how* a session is detected as having started in the first place —
that logic is unchanged since v0.2.

Real-world logging surfaced a case where a shower's ambient starting
humidity was ~58%. The flat threshold didn't fire until humidity crossed
75% — several minutes and roughly 18 points of real rise into the shower.
Because `active_since_humidity` (the baseline `DecisionEngine`'s delta
policy measures rise from) is captured at the moment of that crossing, all
~18 points of pre-crossing rise were invisible to the delta policy. The
effective tolerated rise before a cutoff was therefore `max_humidity_delta`
*plus* however much rise happened to occur before the flat floor was
crossed — silently and unpredictably looser than the configured value, and
worse the higher the room's ambient humidity that day.

A flat floor also doesn't adapt to conditions: a bathroom that runs warm and
humid in summer reaches 75% far sooner than one in a dry winter house, even
with identical shower behavior. This is a distinct problem from the one
ADR-0003 solved — that ADR is about whether an already-exceeded delta is
trustworthy enough to cut; this one is about whether the delta itself is
being measured from the right starting point.

## Decision

Session start is now relative to a tracked **ambient baseline** instead of a
fixed absolute value:

- While `IDLE`, `SessionDetector` maintains a time-based exponential moving
  average (EMA) of humidity — the ambient baseline. The EMA is time-based
  (not sample-based) so irregular sensor push intervals don't distort the
  smoothing.
- A session starts once a reading is `humidity_start_delta` points (default
  3.0) above that baseline.
- The baseline is **frozen** the instant a session starts, and
  `active_since_humidity` is set to that frozen baseline value — not the raw
  reading that crossed the trigger. This is the actual fix: `DecisionEngine`'s
  delta-cutoff policy (and, downstream of ADR-0003, the presence-confirmation
  gate on that policy) now sees the full rise from true ambient conditions,
  not just the portion after an arbitrary floor was crossed.
- The frozen threshold (`baseline + humidity_start_delta` at start time) is
  reused for the existing ACTIVE→COOLDOWN/COOLDOWN→ACTIVE hysteresis, exactly
  as the old flat threshold was — only the *start* trigger became relative.
- The baseline resumes tracking (via the same EMA) once a session ends,
  rather than requiring an explicit reset — it naturally catches back up to
  real ambient conditions over roughly one time constant.
- `max_humidity_delta` (ADR-0003's cutoff, presence-gated) is **not**
  changed by this ADR. The full rise being visible now is a strictly more
  accurate input to that policy; whether the existing default is still the
  right tuning is a separate question, tracked in `BACKLOG.md` pending more
  replayed real-session data.
- The published `binary_sensor.shower_guard_session_active` entity (new in
  this ADR) replaces the packaged YAML's own hardcoded `>= 75.0` template
  sensor, since there's no longer a static number a template can compare
  against — session state is a stateful computation only `session.py` can
  produce. This closes the `BACKLOG.md`/dashboard item tracking that
  duplication as architectural debt.

## Consequences

- A session's first-ever humidity reading can never itself trigger a start —
  it only seeds the baseline. This is a behavior change from all prior
  versions (where a single reading at/above 75% started a session
  immediately); test suites were updated accordingly.
- `SessionDetector`'s constructor and public config surface changed:
  `humidity_threshold` → `humidity_start_delta`, plus a new
  `baseline_time_constant_seconds`. This is a breaking config change — any
  live `configuration.yaml` referencing the old key must be updated by hand;
  this ADR does not migrate live HA configuration. `replay.py`'s CLI flag
  changed the same way (`--humidity-threshold` → `--humidity-start-delta`).
- `decision.py` is untouched — this ADR is scoped entirely to Session
  Detection's start trigger, consistent with ADR-0001's layer separation.
  ADR-0003's presence-confirmation gate behaves identically; it simply now
  receives a more accurate `active_since_humidity`.
- A starting-humidity-dependent (nonlinear) scaling of `max_humidity_delta`
  was discussed but explicitly deferred — see `BACKLOG.md`. One real session
  isn't enough data to justify a formula.

## Alternatives Considered

- **Keep the flat threshold, just lower it** — Rejected. Doesn't generalize
  across seasons/ambient conditions; a lower flat value would just move the
  same problem to a different humidity level rather than fixing it.
- **Require presence AND baseline-rise to start a session** — Considered and
  rejected. Would make session *detection* depend on a sensor that's
  optional everywhere else in the pipeline (and, per ADR-0003, presence's
  role is specifically to gate a cutoff decision, not to gate detection
  itself). Presence-as-confirmation-gate (ADR-0003) and baseline-relative
  start (this ADR) are deliberately independent mechanisms addressing
  different failure modes.
- **Nonlinear/starting-humidity-dependent delta cutoff** — Rejected for this
  ADR; deferred to `BACKLOG.md` pending more replayed real-session data.
