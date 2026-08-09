# ADR-0006 — Presence-Corroborated Decline Can End a Session Directly From ACTIVE

| Field     | Value                    |
|-----------|--------------------------|
| Status    | Accepted                 |
| Date      | 2026-08-08               |
| Author    | Technical Lead           |

---

## Context

ADR-0005 fixed a real incident where a session's binary sensor stayed `on`
for over two hours because `COOLDOWN → ENDED` relied purely on an elapsed
timer that kept getting reset by a shallow decay tail re-crossing the frozen
`session_start_threshold`. That fix — decline-from-peak detection,
optionally corroborated by presence — was deliberately scoped to `COOLDOWN`
only. Its own "Alternatives Considered" section explicitly rejected
extending the same checks to `ACTIVE`, reasoning that the reported problem
was specifically about time spent stuck *in* `COOLDOWN`, and that a session
should keep needing to cross the frozen threshold before any end-detection
logic applies at all.

Real-world history logging from the following day surfaced a related but
distinct failure this reasoning didn't anticipate: **a session that never
reaches `COOLDOWN` in the first place.**

A shower started at 03:39, peaked at 84.4% RH, and declined steadily for
over an hour afterward — the exact trend pattern ADR-0005 was built to
recognize. But it declined only down to a **54–59% plateau**, and the
frozen `session_start_threshold` for that session was **~52–53%**
(`baseline + humidity_start_delta`, with the tight default
`humidity_start_delta` of 3.0). Because the plateau never dropped back
below that threshold — not once, for the remaining eight-plus hours of
logged history — `_from_active` never transitioned to `COOLDOWN`, and every
one of ADR-0005's decline/presence checks, which live entirely inside
`_from_cooldown`, never ran. `binary_sensor.shower_guard_session_active`
stayed `on` all day.

Meanwhile, the presence sensor read continuously `False` from 03:54 to
05:01 — a 67-minute clear stretch starting just five minutes after the
humidity peak. Presence data conclusively showed the room was empty; only
`session.py`'s reliance on first crossing an absolute floor kept that
signal from being used.

This is a narrower and stronger case than the one ADR-0005 rejected.
ADR-0005's alternative considered applying *all* of its end-detection
paths to `ACTIVE`, including sustained decline **without** presence
corroboration — and rightly worried that would let ordinary humidity noise
end a session someone is still actively showering in. This ADR extends
**only** the presence-corroborated path (declined-from-peak **and**
presence continuously clear for `presence_clear_confirm_seconds`) to run
from `ACTIVE`, and leaves the no-presence decline path exactly where
ADR-0005 put it. Presence being confirmed absent is independent,
external evidence that no one is in the room — not a proxy for "humidity
is doing something typical of showers ending" the way an unconfirmed
decline is.

## Decision

`SessionDetector` now tracks the decline-from-peak and presence-clear
timers (`_decline_since`, `_presence_clear_since`) continuously from the
moment a session becomes `ACTIVE`, not only after it reaches `COOLDOWN`.
The tracking logic itself is unchanged from ADR-0005 — it's extracted into
a shared helper (`_update_decline_and_presence_tracking`) so `_from_active`
and `_from_cooldown` run the identical computation.

**In `_from_active`:** after peak-tracking, if `declined` (humidity has
fallen `humidity_decline_delta` points from the session's peak) **and**
`presence_confirmed_clear` (presence has read continuously `False` for
`presence_clear_confirm_seconds`) are both true, the session ends
immediately — transitioning straight from `ACTIVE` to `IDLE`, skipping
`COOLDOWN` entirely, since presence corroboration already serves as the
confirmation `COOLDOWN` normally exists to provide. This check runs before
the existing absolute-threshold check, so whichever condition is met first
determines the outcome.

**In `_from_cooldown`:** unchanged in effect. The sustained-decline-without-
presence path (`decline_confirmed`) still only fires here — i.e., only
once humidity has already dropped below the frozen absolute threshold —
preserving ADR-0005's safety margin against ending a session that's still
genuinely running.

No configuration keys, wiring (`__init__.py`), or `replay.py` changes were
needed: presence was already threaded into every `detector.update()` call
site from ADR-0005, for exactly this kind of extension.

### What changes

| File         | Change |
|--------------|--------|
| `session.py` | New `_update_decline_and_presence_tracking()` helper shared by `_from_active` and `_from_cooldown`. New `_end_session()` helper consolidating the ENDED cleanup + transition, callable from either state. `_from_active` gains a `presence` parameter and the presence-corroborated early-exit check. `update()`'s ACTIVE dispatch now passes `presence` through. Docstrings updated to describe the extended state machine. |
| `const.py`, `__init__.py`, `manifest.json` | Version bump only (1.7.0) — no new config keys, no wiring changes. |
| Tests | New tests in `test_session.py` covering: a session ending directly from `ACTIVE` on presence-corroborated decline; the same decline with presence `True`/unknown *not* ending it early; decline alone (no presence) *not* ending it from `ACTIVE` even when sustained past `decline_confirm_seconds`; and a regression test replaying the real incident's humidity/presence trace end-to-end. |

## Consequences

- A session whose post-shower residual humidity never drops back below the
  frozen absolute threshold — previously stuck `ACTIVE` indefinitely — now
  ends as soon as presence is confirmed clear and a decline from peak is
  underway, regardless of the absolute humidity level. This directly fixes
  the logged incident.
- Deployments **without** a presence sensor gain nothing from this ADR.
  `presence_confirmed_clear` can never be true when `presence` is always
  `None`, so a session with no presence sensor and a residual-humidity
  plateau above the frozen threshold can still get stuck `ACTIVE`
  indefinitely. This is a known, deliberate limitation — see Backlog.
- The two-path split (presence-corroborated from `ACTIVE`; decline-alone
  restricted to `COOLDOWN`) means a reader now needs to hold both
  `_from_active` and `_from_cooldown` in mind to answer "what ends a
  session," rather than just `_from_cooldown`. The shared helper keeps the
  actual decline/presence computation in one place to limit this cost.
- `COOLDOWN` can now be skipped entirely for a session that ends via the
  presence-corroborated path from `ACTIVE`. `RESUMED` therefore cannot fire
  for such a session (there was no `COOLDOWN` to resume from) — this is
  correct: presence confirmed the room empty, so there is nothing to
  "resume" until a genuinely new `STARTED` crossing occurs.
- A brief presence blip shortly after a real end-of-session event (e.g.
  someone stepping back in to grab a towel while residual humidity is
  still elevated) can now register as a second, short `STARTED`/`ENDED`
  pair rather than being absorbed into one long session. Replaying the
  logged incident shows exactly this: the original session ends at 03:59,
  a second short session runs 04:00–04:08, and the detector correctly
  settles into `IDLE` for the rest of the day. This is judged more
  accurate than the alternative (one session silently spanning both
  events), and is consistent with how any other presence-driven
  `RESUMED` already behaves.

## Alternatives Considered

- **Also extend the no-presence `decline_confirmed` path to `ACTIVE`** —
  Rejected, for the same reason ADR-0005 rejected it for `COOLDOWN`-adjacent
  reasoning applied more broadly: without presence corroboration, a
  temporary dip in humidity while someone is still actively showering
  (adjusting water temperature, stepping momentarily out of the direct
  spray) is indistinguishable from a real decline using humidity alone.
  Presence is what makes trusting an early decline safe; without it, the
  existing requirement to first cross the frozen threshold remains the
  right guard.
- **Lower `humidity_start_delta` reuse — stop freezing the absolute
  threshold at session start; keep tracking the ambient baseline through
  ACTIVE/COOLDOWN too, and use `baseline + start_delta` live** — Rejected
  as a separate, larger change with its own risks (a rising ambient
  baseline during a long, hot, well-attended shower could itself cause a
  session to look "resumed" indefinitely as its own residual heat lifts the
  baseline it's being measured against). Left as a possible future ADR if
  the presence-corroborated fix here proves insufficient across more
  logged sessions.
- **Add a `max_active_seconds` hard cap, independent of humidity/presence,
  as a backstop** — Deferred, not rejected. This ADR's fix resolves the
  logged incident for deployments with a presence sensor (David's chalet).
  A duration-based backstop would also help the no-presence case this ADR
  explicitly doesn't cover, but is an orthogonal, simpler mechanism worth
  its own ADR rather than folding in here. Tracked in `BACKLOG.md`.
