# ADR-0005 — Decline-Confirmed Session End (Trend, Gated by Presence When Available)

| Field     | Value                    |
|-----------|--------------------------|
| Status    | Accepted                 |
| Date      | 2026-08-07               |
| Author    | Technical Lead           |

---

## Context

Since v0.2, `SessionDetector` has ended a session (`COOLDOWN` → `ENDED`)
purely on an elapsed timer: once humidity drops below the frozen
`session_start_threshold`, the session ends `cooldown_seconds` (default 300s)
later, unconditionally. ADR-0004 made the *start* trigger baseline-relative
but left this end-of-session timer untouched.

Real-world history logging surfaced a case where this interacts badly with
ADR-0004's lower, ambient-relative threshold. A session peaked at ~83% RH and
then decayed slowly over more than two hours, but `session_start_threshold`
(baseline + 3.0 points, per ADR-0004) sat much lower than the old flat 75%
floor — around 60% in this case. During a long decay tail, humidity
re-crossed that lower threshold repeatedly on the way down, each crossing
firing `RESUMED` and resetting `_cooldown_start`. `binary_sensor.shower_guard_
session_active` stayed `on` for the entire two-hour decay, even though
presence had gone `clear` and humidity had been falling since minutes after
the peak. ADR-0004 made the start trigger more accurate at the cost of making
the *hysteresis band it reuses for end-detection* easier to re-cross during a
long, shallow decay — a side effect that ADR wasn't scoped to catch.

The elapsed-timer approach also throws away two signals already available
that indicate a session is genuinely over, not just paused:

1. **Humidity trend.** A sustained decline from the session's peak is a
   direct sign the room is drying out — stronger evidence than "still below
   an arbitrary crossing point" and unrelated to where that point happens to
   sit.
2. **Presence**, when a sensor is configured. The bathroom being confirmed
   empty is strong corroboration that a humidity decline reflects the room
   clearing out, not sensor noise — and can end a session faster than
   waiting out a fixed timer.

## Decision

`COOLDOWN` → `ENDED` now fires on the **first** of three conditions, checked
in order, instead of solely on elapsed time:

1. **Decline + presence confirmed (fastest path, requires a presence
   sensor).** Humidity has fallen `humidity_decline_delta` points (default
   1.0) or more from the session's peak, **and** presence has been
   continuously `False` for at least `presence_clear_confirm_seconds`
   (default 60s). No additional decline debounce is applied here — presence
   corroboration already filters out spurious single-reading dips, and this
   path exists specifically to be fast.
2. **Sustained decline alone (no presence sensor required).** Humidity has
   fallen `humidity_decline_delta` points or more from the peak, and that
   decline has held continuously for at least `decline_confirm_seconds`
   (default 60s). This is the path for deployments without a presence
   sensor, or where presence is still `True`/unknown. The sustain window
   exists because, without presence corroborating it, a single noisy dip
   (a stray reading, a brief fan cycle) must not be allowed to end a session
   that's still running.
3. **Stable/elapsed-timeout fallback (existing behavior, unchanged
   trigger).** If neither of the above fires — humidity is flat rather than
   declining, or is oscillating without a sustained trend — `cooldown_seconds`
   elapsed since entering `COOLDOWN` still ends the session, exactly as
   before. This is what actually implements "stable for 5 minutes ⇒ over"
   for the no-presence, no-clear-decline case: if humidity isn't declining
   and isn't rising back above the threshold, it's flat, and the existing
   timer already captures that.

The session's **peak humidity** since `active_since` is now tracked
(`_peak_humidity`), reset the same way `active_since_humidity` already is —
frozen relative to the current session, reset to the raw reading on
`RESUMED` so a sibling's fresh shower isn't measured against the previous
person's peak.

`SessionDetector.update()` gains an optional `presence: Optional[bool] =
None` parameter, mirroring how `presence` already flows into
`DecisionEngine.evaluate()`. This is the one deliberate crossing of
previously-separate data: presence was Decision Engine input only before
this ADR. It remains a plain `bool`/`None` value passed in by the wiring
layer — `session.py` gains no HA imports and stays a pure computation,
satisfying ADR-0001's constraint even though presence's *scope* changes.

### What changes

| File           | Change                                                                 |
|----------------|-------------------------------------------------------------------------|
| `const.py`     | Add `CONF_HUMIDITY_DECLINE_DELTA` / `DEFAULT_HUMIDITY_DECLINE_DELTA` (1.0), `CONF_DECLINE_CONFIRM_SECONDS` / `DEFAULT_DECLINE_CONFIRM_SECONDS` (60.0), `CONF_PRESENCE_CLEAR_CONFIRM_SECONDS` / `DEFAULT_PRESENCE_CLEAR_CONFIRM_SECONDS` (60.0). |
| `session.py`   | Track `_peak_humidity`, `_decline_since`, `_presence_clear_since`. `update()` gains optional `presence`. `_from_cooldown` implements the three-condition check above instead of elapsed-time-only. `decision.py` is untouched — this ADR is scoped entirely to Session Detection's end trigger, same boundary ADR-0004 drew for the start trigger. |
| `__init__.py`  | Pass `hass.data[DOMAIN]["presence"]` into `detector.update()` on every humidity reading. The presence callback now also re-runs `detector.update()` against the last known humidity (mirroring how it already re-runs `_evaluate_and_record` for the Decision Engine), so a session can end immediately when presence clears mid-decline rather than waiting for the next humidity push. |
| `replay.py`    | Thread `current_presence` (already tracked for `DecisionEngine.evaluate()`) into `detector.update()` too. New optional `replay()` parameters and CLI flags for the three new constants. |
| Tests          | New tests in `test_session.py` covering decline-only end, presence-confirmed fast end, single-dip noise *not* ending a session, and peak-reset on `RESUMED`. `test_init.py` gains a test that a presence-clear event alone (no new humidity reading) can end an already-declining session. `test_replay.py` unaffected defaults verified to still pass unchanged. |

### Configuration

```yaml
shower_guard:
  humidity_sensor: sensor.bathroom_humidity
  presence_sensor: binary_sensor.bathroom_presence   # optional, as before
  humidity_decline_delta: 1.0                        # optional — default 1.0
  decline_confirm_seconds: 60                        # optional — default 60
  presence_clear_confirm_seconds: 60                 # optional — default 60
  cooldown_seconds: 300                              # unchanged — now the stable/timeout fallback only
```

## Consequences

- Sessions end sooner, and more accurately, once a real decline is
  underway — the two-hour "stuck Running" case this ADR was written to fix
  no longer occurs, since sustained decline (with or without presence) now
  beats waiting out the full `cooldown_seconds` timer.
- Without a presence sensor, ending still requires a *sustained* decline
  (`decline_confirm_seconds`), not an instantaneous one — a single noisy
  reading cannot end a session on its own. This is strictly more permissive
  than the old pure-timer behavior for a genuinely declining session, and
  identical for a flat/oscillating one (path 3 still applies).
- `SessionDetector.update()`'s signature grows by one optional, backward-
  compatible parameter. Existing callers that don't pass `presence` behave
  exactly as if no presence sensor were configured (path 1 above can never
  fire; paths 2 and 3 are unaffected by this change on their own).
- Presence's role now spans two files instead of one (`session.py` in
  addition to `decision.py`). Both consume it as a plain `Optional[bool]`
  with no HA import, so ADR-0001's pure-layer constraint holds — but a
  reader auditing "where does presence matter" now needs to check both
  files instead of one.
- The `RESUMED` reset now also clears `_peak_humidity`, `_decline_since`,
  and `_presence_clear_since`, alongside the existing `active_since_humidity`
  reset — consistent with the existing "sibling shower gets a fresh
  baseline" behavior, extended to the new tracked state.

## Alternatives Considered

- **Feed presence into `session.py` only via a rolling regression /
  linear-fit slope instead of peak-relative decline** — Rejected for now.
  A true rolling-slope trend detector is more robust to noise but adds real
  complexity (window sizing, minimum sample count) for a problem the
  simpler peak-relative-plus-sustain-window approach already solves given
  the logged data. Left as a possible future refinement if peak-relative
  decline proves too coarse once more real sessions are captured — tracked
  in `BACKLOG.md`.
- **Drop the elapsed-timer fallback entirely, always require decline or
  presence** — Rejected. A session where humidity plateaus without clearly
  declining (e.g. a slow-draining, well-ventilated bathroom) would then
  never end. The timer remains the guaranteed backstop.
- **Apply the same fast paths while still `ACTIVE`, not just in
  `COOLDOWN`** — Rejected. `ACTIVE` → `COOLDOWN` already requires dropping
  below the frozen `session_start_threshold`; layering decline/presence
  checks on top of `ACTIVE` as well would let a session end without ever
  entering `COOLDOWN`, changing the state machine's shape for no clear
  benefit — the reported problem is specifically about time spent stuck in
  `COOLDOWN`.
- **Presence-clear alone (no decline requirement) ends a session** —
  Rejected. Presence sensors can read `False` while someone is genuinely
  still showering behind a closed curtain/door depending on placement;
  requiring the decline alongside presence keeps humidity as the primary
  signal and presence as corroboration, not a substitute.
