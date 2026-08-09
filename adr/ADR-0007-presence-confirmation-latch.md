# ADR-0007 — Presence Confirmation Latches Per Delta Baseline

| Field     | Value                    |
|-----------|--------------------------|
| Status    | Accepted                 |
| Date      | 2026-08-08               |
| Author    | Technical Lead           |

---

## Context

ADR-0003 gates the humidity-delta water cut on presence: water is cut only
once `humidity_delta >= max_humidity_delta` **and** presence has been seen
`True` within `presence_confirmation_window_seconds` (default 60s). The
intent, per ADR-0003, is to confirm the humidity rise is actually caused by
an occupied shower — ruling out a stray humidity source (weather, cooking
steam) being mistaken for one. Presence is a confirmation gate, not an
independent trigger.

`DecisionEngine.evaluate()` re-derives this confirmation fresh on every
call, purely from `now - last_presence_at`. This works fine for a presence
sensor that reads reliably `True` throughout an occupied shower. It does not
hold up against the mmWave sensor actually deployed: `history.csv` from the
same incident investigated in ADR-0006 shows the presence sensor toggling
`False`/`True` every 20–100+ seconds throughout an actual, continuous,
attended shower — a well-known mmWave failure mode where a person standing
relatively still (washing, rinsing) looks like "no motion" to the radar.

Several of those gaps exceeded the 60s confirmation window. Replaying the
logged humidity and presence values through the actual (unchanged)
`DecisionEngine` shows the consequence directly: the decision toggled
`WATER_CUT` → `WATER_AVAILABLE` → `WATER_CUT` roughly six times across a
13-minute span, while the person was still in the shower and the humidity
delta remained far above threshold throughout. Each toggle would have fired
`script.shower_guard_cut_water` or `script.shower_guard_restore_water`
again — cycling a physical valve/pump repeatedly during active use, with
the attendant risk of cold-water bursts for the person and unnecessary wear
on the actuator hardware.

This is a distinct problem from ADR-0006's. ADR-0006 fixed Session
Detection getting stuck because it never re-evaluated at all. This is the
opposite failure: the Decision Engine re-evaluates the presence
confirmation on every single call, more eagerly than the actual sensor
hardware can reliably support, and treats "no recent True reading" as
equivalent to "this humidity rise is now unconfirmed" even when a person is
demonstrably still there.

## Decision

`DecisionEngine` now latches presence confirmation per delta baseline.
Once presence has been confirmed (via either check already in ADR-0003 —
currently `True`, or seen `True` within the window) for the *current*
`active_since_humidity` baseline, that confirmation holds even if a later
call finds presence neither currently `True` nor recently confirmed. A
momentary sensor gap can no longer, by itself, flip an already-confirmed
cut back to available while the delta remains exceeded.

The latch is internal engine state (`_latched_baseline`,
`_presence_confirmed_latch`), reset whenever the baseline itself resets —
i.e. whenever `active_since_humidity` changes (a fresh `STARTED`, or a
`RESUMED` for a sibling's shower, which already gets its own fresh delta
baseline per the existing "sibling shower" behavior) — or whenever
`session_state` is `IDLE` / `active_since` is `None` (no active session at
all, so any future session starts fully unconfirmed).

The latch does not override genuine humidity decline: it's only checked
when `delta >= max_humidity_delta`. If humidity itself drops back under
threshold, the decision correctly returns to available on that basis alone
— the latch has no say in that path. If delta *rises back above* threshold
later within the same, still-active baseline, the existing latch (from an
earlier confirmation this same baseline) still applies without needing a
fresh presence sighting — reasonable, since nothing about *not confirming
presence a second time* casts doubt on a confirmation already established
this baseline.

No new configuration keys were introduced. `presence_confirmation_window_
seconds` keeps its existing meaning and default — it still governs how
recent a sighting must be for the *first* confirmation. It's the ongoing
re-litigation of that confirmation on every subsequent call that this ADR
removes.

### What changes

| File            | Change |
|-----------------|--------|
| `decision.py`   | `DecisionEngine.__init__` gains `_latched_baseline` / `_presence_confirmed_latch` instance state. `evaluate()` resets both on `IDLE`/no-session and whenever `active_since_humidity` differs from the latched value; sets the latch `True` the first time presence is confirmed for the current baseline; checks the latch (not just the instantaneous confirmation) before deciding to cut. `DecisionResult.reason` distinguishes a fresh confirmation from a latched one. |
| Tests           | New tests in `test_decision.py`: latch persisting a cut through a later out-of-window gap; the latch never firing before a first real confirmation; the latch resetting on a fresh baseline (sibling `RESUMED`); the latch resetting on return to `IDLE`; a genuine delta decline still overriding the latch, with the latch still holding if delta rises again within the same baseline; and a regression test replaying the exact logged gap pattern (two ~100s+ presence gaps during one continuous shower) confirming the decision no longer flaps. |

## Consequences

- A real, continuously-attended shower with a flaky presence sensor no
  longer causes the water valve/pump to be cycled repeatedly mid-shower.
  Replaying the full logged incident (Session Detection fix from ADR-0006
  plus this fix together) drops the day's total decision changes from
  roughly 9 (7 flapping toggles during the shower, plus the stuck-active
  non-resolution) down to 3: idle → cut (at the real start of excessive
  humidity) → available (at the real, correctly-detected end of the
  session) → stays available.
- `DecisionEngine` is no longer a fully stateless pure function of its
  arguments alone — it now carries a small, explicit, well-scoped piece of
  internal state across calls. This is a real departure from its prior
  "pure computation" framing (see ADR-0001's actuator-agnostic layering,
  which this doesn't affect, and the class's own docstring, updated here).
  The state is deliberately minimal and fully determined by
  `active_since_humidity`, which the engine already receives every call —
  no hidden inputs, and `replay.py` needs no changes since it already
  constructs one `DecisionEngine` instance per run and calls `evaluate()`
  in order, which is exactly what the latch assumes.
- A confirmed cut can now persist based on a presence sighting that's
  arbitrarily old, as long as the baseline hasn't reset and delta remains
  exceeded. This trades a small amount of "freshness" for a large
  reduction in false restores — judged the right tradeoff given ADR-0003's
  own stated purpose (confirm *causation*, not track *current* occupancy
  instant-by-instant). If a person is confirmed present once during a
  session and then genuinely leaves without the session's humidity ever
  declining (e.g. leaving the water running unattended after that first
  confirmation), this latch means presence loss alone won't re-cut it a
  second way — but this exact scenario is already independently covered:
  a genuinely abandoned running shower's humidity keeps rising or holds
  near peak, and Session Detection's own decline-based end (ADR-0005/
  ADR-0006) does not fire without an actual decline, so the session (and
  therefore the cut, since delta stays exceeded) simply continues rather
  than the water being restored.

## Alternatives Considered

- **Increase `presence_confirmation_window_seconds` (e.g. to 120–180s)
  instead of latching** — Rejected as a blunt fix. It would reduce
  flapping for this specific sensor's gap sizes without addressing the
  root cause (re-litigating a confirmation that shouldn't need
  re-litigating), and would need continual retuning against whatever gap
  distribution a given presence sensor happens to produce. A longer window
  also does nothing for a gap that happens to exceed it.
- **Track a separate, longer "restore confirmation" window instead of a
  true latch (fast-trip, slow-release hysteresis)** — Considered
  seriously, and functionally similar for the common case. Rejected in
  favor of the simpler latch because ADR-0003's own stated purpose for
  requiring presence is causation confirmation, not real-time occupancy
  tracking — once causation is confirmed for this baseline, there's no
  principled reason to require re-confirming it later, rather than just
  requiring it more slowly. A slow-release window would also add a second
  tunable duration with unclear default-value guidance; the latch needs
  none.
- **Move the confirmation logic into Session Detection (`session.py`)
  instead of `DecisionEngine`** — Rejected. Presence-as-causation-
  confirmation for the *water-cut decision* is squarely Decision Engine's
  responsibility per ADR-0001's layering; Session Detection's own use of
  presence (ADR-0005/ADR-0006) is for a different question (has the
  session ended), and conflating the two would blur that boundary for no
  benefit.
