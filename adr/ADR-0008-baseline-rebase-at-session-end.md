# ADR-0008 — Rebase Ambient Baseline to the Ending Reading at Session End

| Field     | Value                    |
|-----------|--------------------------|
| Status    | Accepted                 |
| Date      | 2026-08-09               |
| Author    | Technical Lead           |

---

## Context

`SessionDetector._baseline` (the ambient EMA tracker, ADR-0004) is only
updated while `IDLE` — `_update_baseline()` is never called during
`ACTIVE`/`COOLDOWN`. Before ADR-0006, this was harmless: a session could
only reach `IDLE` once humidity had already decayed close to the frozen
`session_start_threshold`, so by the time tracking resumed, the stale
pre-session baseline and the actual current humidity were already close
together, and the EMA's catch-up was a minor correction.

ADR-0006 deliberately breaks that assumption: a session can now end via the
presence-corroborated path while humidity is still far above true ambient
(78% and 80% in the two incidents that motivated ADR-0006). That leaves the
baseline needing to "catch up" from a genuinely stale, pre-shower value
toward a much higher current reading — and that catch-up happens through
`_update_baseline()`'s time-based EMA on whatever reading happens to arrive
first back in `IDLE`.

The completeness of that catch-up depends on `dt`, the time since
`_baseline_updated_at` was last touched — which is approximately **how long
the just-ended session ran**, not how long ago it ended, since the baseline
was frozen the entire time. This produces genuinely unpredictable behavior
for whoever's next:

- **A long preceding session** (the logged incidents ran ~20 minutes)
  produces a large `dt`, so the EMA's `alpha` approaches 1 and the baseline
  jumps most of the way to the current reading on the very first post-`IDLE`
  update — happening to land close to correct, but only by accident of
  session length.
- **A short preceding session** (e.g. ADR-0006 catching an early false
  start within a couple of minutes) produces a small `dt` and a small
  `alpha`, leaving the baseline stuck near its stale pre-shower value. A
  second person's real shower starting shortly after would then compute its
  humidity delta against that stale low baseline — their actual, modest
  rise could already look like a delta far past `max_humidity_delta`,
  cutting their water almost immediately despite them having barely started.
- The opposite failure is also possible: if the EMA catch-up happens to land
  close to the second person's own actual humidity, their real rise might
  not clear `humidity_start_delta` for a while, delaying `STARTED` detection
  for their genuinely running shower.

This was flagged as a concrete concern: two people showering back-to-back
in the same bathroom (e.g. a sibling right after another), where the first
session ends — possibly quickly, via ADR-0006 — while the room is still
humid, and the second person's shower needs to be assessed on its own
merits rather than inheriting an artifact of exactly how the first session's
timing happened to interact with the baseline's EMA time constant.

Notably, this exact class of problem — a fresh, accurate reference point
needed for whoever showers next — was already solved once, for a narrower
case: `RESUMED` (a sibling starting during the same session's `COOLDOWN`
window) already resets `active_since_humidity` to the raw current reading,
not the original session's stale start baseline, specifically so "a sibling
starting a fresh shower during the cooldown window gets its own baseline
rather than inheriting the previous person's cumulative rise." This ADR
extends the same accuracy guarantee across the brief `IDLE` gap that occurs
once a session has fully ended, rather than only within the same session's
`COOLDOWN`.

## Decision

`_end_session()` now rebases the ambient baseline to the exact reading at
the moment the session ends: `self._baseline = humidity` and
`self._baseline_updated_at = now`, alongside the existing session-scoped
state cleanup. This applies uniformly regardless of which path ended the
session — the ADR-0006 presence-corroborated path from `ACTIVE`, the
ADR-0005 decline-confirmed or presence-corroborated paths from `COOLDOWN`,
or the original elapsed-timeout fallback — since all of them funnel through
`_end_session()`.

After the rebase, `_update_baseline()`'s ordinary time-based EMA resumes
from that accurate starting point, tracking further ambient drift (e.g. the
residual humidity continuing to dissipate) exactly as it's meant to. The
fix doesn't change what the EMA does — only what it starts from.

This means a session starting shortly after another one ended gets:

1. **A correct `STARTED` trigger point** — `humidity_start_delta` above the
   *actual* room condition when the previous session ended (and however it
   has drifted since), not above a stale value from potentially hours
   earlier.
2. **A correct delta baseline** for the Decision Engine's cutoff —
   `active_since_humidity` freezes to the (now-accurate) baseline at the
   new session's start, so the new occupant's own humidity contribution is
   what's measured against `max_humidity_delta`, not an inherited gap.

No new configuration keys were introduced.

### What changes

| File         | Change |
|--------------|--------|
| `session.py` | `_end_session()` additionally sets `self._baseline` and `self._baseline_updated_at` to the ending reading. `_update_baseline()`'s docstring updated to describe the narrower role it now plays (ordinary idle-time drift tracking, not correcting a stale freeze). Module docstring updated. |
| `const.py`, `__init__.py`, `manifest.json` | Version bump only (1.9.0) — no new config keys, no wiring changes. |
| Tests | New tests in `test_session.py`: baseline snaps exactly to the ending reading; a deliberately *short* first session (the worst case under the old EMA-catch-up behavior) still produces an exact, immediate baseline correction regardless of how little time elapsed; and a regression test simulating a second shower starting shortly after the first ends at high humidity, confirming the second session's own delta baseline reflects the accurate current condition rather than the stale original ambient. |

## Consequences

- A second shower starting shortly after another ends now gets a fair,
  accurate assessment of its own humidity contribution, regardless of how
  long the first session ran or how it ended. Simulating the originally
  reported concern (a session starting right after ADR-0006 ends one at
  high humidity) confirms the fix: the second session's `STARTED` fires on
  a genuine fresh rise, and its cut fires only once its own delta —
  measured from the accurate, current baseline — genuinely exceeds
  `max_humidity_delta`.
- This also incidentally eliminates the harmless-but-confusing spurious
  secondary `STARTED`/`ENDED` blip observed in the first ADR-0006 incident
  replay (a brief second "session" a minute after the first one ended, an
  artifact of the old partial EMA catch-up creating a residual gap just
  over `humidity_start_delta`). That blip never represented real risk (no
  cut ever fired during it), but its disappearance is a useful confirmation
  the rebase is working as intended.
- `_update_baseline()`'s EMA now only ever operates over genuinely idle
  periods with no session in between, which is the condition its time
  constant (`baseline_time_constant_seconds`, default 600s / 10 min) was
  actually tuned for. Previously, that same time constant was implicitly
  (and inconsistently) also governing "how fast do we recover from being
  stale after a session," a job it was never designed for and could not do
  reliably.
- The rebase happens even for a session that ends the "normal" way (decline
  already brought humidity close to the original baseline). In that case
  the rebase is a near no-op — the ending reading is already close to what
  the EMA would have converged to anyway — so this doesn't change behavior
  for the common case, only the case ADR-0006 specifically introduced.

## Alternatives Considered

- **Only rebase when the session ended via the ADR-0006 ACTIVE-state path
  (the specific case that introduced the risk), leaving COOLDOWN-based
  endings on the old EMA-catch-up behavior** — Rejected as unnecessary
  complexity. A COOLDOWN-based ending's rebase is a near no-op anyway (see
  Consequences), so there's no behavioral cost to applying it uniformly,
  and a single code path in `_end_session()` is simpler to reason about
  than two.
- **Increase `baseline_time_constant_seconds` responsiveness (a shorter
  default) instead of rebasing** — Rejected. A shorter time constant speeds
  up the EMA's catch-up generally, but doesn't fix the fundamental issue
  that the catch-up's completeness is *tied to how long the previous
  session ran* rather than being independent of it — it would narrow the
  gap for long-session cases while barely helping the short-session worst
  case, and would also make the baseline noisier during genuine idle
  periods, which the existing 600s default was deliberately chosen to
  smooth over.
- **Extend `RESUMED`'s exact mechanism (reset on humidity crossing back
  above a threshold) across the `IDLE` gap, rather than touching
  `_baseline` at end-of-session** — Considered, but `RESUMED` only fires
  from `COOLDOWN`, which requires still being in the same, not-yet-fully-
  ended session. A session that's already reached `IDLE` (as ADR-0006 now
  allows well before humidity is near ambient) has no `RESUMED`-equivalent
  transition available; rebasing `_baseline` directly at `_end_session()`
  is the natural analog for a session that has genuinely ended rather than
  paused.
