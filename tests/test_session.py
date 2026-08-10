# ---
# purpose: Tests for the Session Detection layer — dynamic ambient baseline
#          replacing the flat absolute humidity threshold (v1.2, ADR-0004),
#          decline/presence-confirmed session end from COOLDOWN (v1.3,
#          ADR-0005), the same presence-corroborated end firing directly
#          from ACTIVE for sessions that never reach COOLDOWN (v1.4,
#          ADR-0006), and rebasing the ambient baseline to the ending
#          reading at session end so a session starting shortly afterward
#          doesn't inherit a stale pre-shower baseline (v1.5, ADR-0008).
# version: 1.5.0
# ---

import sys
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for _mod in (
    "homeassistant",
    "homeassistant.core",
    "homeassistant.config_entries",
    "homeassistant.const",
    "homeassistant.helpers",
    "homeassistant.helpers.event",
):
    sys.modules.setdefault(_mod, MagicMock())

sys.modules["homeassistant.const"].STATE_UNKNOWN = "unknown"

from custom_components.shower_guard.session import (
    SessionDetector,
    SessionEvent,
    SessionState,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

T0 = datetime(2026, 1, 1, 8, 0, 0)

def t(seconds: int = 0) -> datetime:
    """Return T0 + seconds."""
    return T0 + timedelta(seconds=seconds)


def make_detector(
    start_delta: float = 3.0,
    cooldown: int = 300,
    tau: float = 600.0,
    decline_delta: float = 1.0,
    decline_confirm: float = 60.0,
    presence_clear_confirm: float = 60.0,
) -> SessionDetector:
    return SessionDetector(
        humidity_start_delta=start_delta,
        cooldown_seconds=cooldown,
        baseline_time_constant_seconds=tau,
        humidity_decline_delta=decline_delta,
        decline_confirm_seconds=decline_confirm,
        presence_clear_confirm_seconds=presence_clear_confirm,
    )


# ---------------------------------------------------------------------------
# Baseline tracking (IDLE only)
# ---------------------------------------------------------------------------

def test_first_reading_seeds_baseline_and_never_starts_session():
    """The very first reading has no history to compare against — it seeds
    the baseline exactly, and can never itself trigger a start, no matter
    how high it is."""
    d = make_detector()
    result = d.update(humidity=95.0, now=t(0))
    assert result is None
    assert d.state is SessionState.IDLE
    assert d.baseline_humidity == 95.0


def test_baseline_stays_put_under_stable_humidity():
    """Repeated identical readings keep the baseline unchanged and never
    trigger a start."""
    d = make_detector()
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=58.0, now=t(60))
    result = d.update(humidity=58.0, now=t(120))
    assert result is None
    assert d.state is SessionState.IDLE
    assert d.baseline_humidity == 58.0


def test_slow_ambient_drift_does_not_start_a_session():
    """A slow rise (e.g. weather/seasonal drift over an hour) gets absorbed
    into the baseline rather than mistaken for a shower."""
    d = make_detector(start_delta=3.0, tau=600.0)
    d.update(humidity=58.0, now=t(0))
    result = d.update(humidity=61.0, now=t(3600))  # 1 hour later, +3 total
    assert result is None
    assert d.state is SessionState.IDLE


def test_fast_rise_above_baseline_starts_session():
    """A quick rise well above the (barely-moved) baseline starts a session,
    even if the room's ambient humidity that day is far from any fixed
    absolute floor."""
    d = make_detector(start_delta=3.0, tau=600.0)
    d.update(humidity=58.0, now=t(0))          # seed baseline ~58
    d.update(humidity=58.0, now=t(300))        # stays ~58
    change = d.update(humidity=62.0, now=t(305))  # fast jump 5s later
    assert change is not None
    assert change.event is SessionEvent.STARTED
    assert d.state is SessionState.ACTIVE
    # active_since_humidity is the ambient baseline, not the raw 62.0 reading
    # — this is the fix for the "missing rise" bug: a session starting from
    # a high ambient baseline no longer hides the pre-trigger rise from the
    # Decision Engine's delta-cutoff policy.
    assert d.active_since_humidity < 59.0


def test_baseline_humidity_property_exposes_current_value():
    d = make_detector()
    d.update(humidity=50.0, now=t(0))
    assert d.baseline_humidity == 50.0


def test_ema_time_constant_controls_smoothing_speed():
    """A shorter time constant tracks the ambient baseline faster. Uses a
    high start_delta so this test is purely about baseline smoothing, not
    session start."""
    fast = SessionDetector(humidity_start_delta=50.0, baseline_time_constant_seconds=10.0)
    slow = SessionDetector(humidity_start_delta=50.0, baseline_time_constant_seconds=600.0)

    for d in (fast, slow):
        d.update(humidity=50.0, now=t(0))
        d.update(humidity=60.0, now=t(10))

    assert fast.baseline_humidity > slow.baseline_humidity


def test_baseline_catches_up_after_a_session_ends():
    """After a session ends, the ambient baseline (frozen during the
    session) resumes tracking and converges back to real ambient conditions
    over time — it does not stay pinned to the pre-shower reading forever."""
    d = make_detector(start_delta=3.0, cooldown=10, tau=5.0)
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=70.0, now=t(5))    # STARTED
    d.update(humidity=40.0, now=t(10))   # COOLDOWN
    d.update(humidity=40.0, now=t(20))   # ENDED

    assert d.state is SessionState.IDLE
    d.update(humidity=40.0, now=t(80))   # a minute of fresh air later
    assert d.baseline_humidity < 41.0


# ---------------------------------------------------------------------------
# ACTIVE state — frozen session_start_threshold provides hysteresis
# ---------------------------------------------------------------------------

def test_active_stays_active_while_above_frozen_threshold():
    d = make_detector(start_delta=3.0)
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=62.0, now=t(5))   # STARTED, threshold frozen ~= 58+3
    result = d.update(humidity=90.0, now=t(65))
    assert result is None
    assert d.state is SessionState.ACTIVE


def test_active_enters_cooldown_when_dropping_below_frozen_threshold():
    d = make_detector(start_delta=3.0)
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=62.0, now=t(5))   # STARTED
    result = d.update(humidity=59.0, now=t(65))  # below ~61 threshold
    assert result is None
    assert d.state is SessionState.COOLDOWN


# ---------------------------------------------------------------------------
# COOLDOWN state
# ---------------------------------------------------------------------------

def test_cooldown_ends_session_after_elapsed():
    d = make_detector(start_delta=3.0, cooldown=300)
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=62.0, now=t(5))            # STARTED
    d.update(humidity=59.0, now=t(65))           # COOLDOWN

    result = d.update(humidity=59.0, now=t(65 + 299))
    assert result is None
    assert d.state is SessionState.COOLDOWN

    change = d.update(humidity=59.0, now=t(65 + 300))
    assert change is not None
    assert change.event is SessionEvent.ENDED
    assert d.state is SessionState.IDLE


def test_cooldown_resumes_on_rise_above_frozen_threshold():
    d = make_detector(start_delta=3.0, cooldown=300)
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=62.0, now=t(5))            # STARTED
    d.update(humidity=59.0, now=t(65))           # COOLDOWN

    change = d.update(humidity=62.0, now=t(120))
    assert change is not None
    assert change.event is SessionEvent.RESUMED
    assert d.state is SessionState.ACTIVE


def test_sibling_shower_gets_fresh_baseline_on_resume():
    """A sibling starting a fresh shower during the cooldown window resets
    the humidity baseline to the RESUMED reading, rather than inheriting the
    first sibling's cumulative rise (unchanged from v1.1 behavior)."""
    d = make_detector(start_delta=3.0, cooldown=300)
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=70.0, now=t(5))            # STARTED, baseline ~58
    d.update(humidity=60.0, now=t(65))           # COOLDOWN

    d.update(humidity=62.0, now=t(120))          # RESUMED (sibling starts)
    assert d.active_since_humidity == 62.0       # fresh baseline, not ~58


# ---------------------------------------------------------------------------
# Decline-confirmed session end (ADR-0005)
#
# All tests here settle the ambient baseline exactly at 58.0 (two identical
# IDLE readings before the session starts) and trigger with a 3.5-point rise
# (humidity_start_delta=3.0), so the session's peak (61.5, the raw trigger
# reading) sits just ~0.5pt above the frozen session_start_threshold
# (~61.03). That small, known gap is what makes 58.0 (ambient) unambiguously
# "declined" (3.5pts below peak) and 60.8 unambiguously "not declined" (only
# 0.7pt below peak) relative to humidity_decline_delta=1.0.
# ---------------------------------------------------------------------------

def _settle_and_start(d: SessionDetector, base_t: int = 0) -> None:
    """Seed the ambient baseline at exactly 58.0, then trigger a session
    with a fast 3.5-point rise to 61.5 (peak=61.5, threshold~=61.03)."""
    d.update(humidity=58.0, now=t(base_t))
    d.update(humidity=58.0, now=t(base_t + 100))  # baseline settles at 58.0 exactly
    change = d.update(humidity=61.5, now=t(base_t + 105))  # STARTED
    assert change is not None and change.event is SessionEvent.STARTED


def test_peak_humidity_tracks_highest_reading_while_active():
    """peak_humidity rises through ACTIVE and is not just the trigger
    reading."""
    d = make_detector(start_delta=3.0)
    _settle_and_start(d)
    d.update(humidity=90.0, now=t(300))  # still ACTIVE (above threshold), new peak
    assert d.peak_humidity == 90.0


def test_sustained_decline_ends_session_before_cooldown_elapses_no_presence():
    """A decline held for decline_confirm_seconds ends the session earlier
    than the full cooldown timer, even with no presence sensor at all."""
    d = make_detector(start_delta=3.0, cooldown=300, decline_delta=1.0, decline_confirm=60)
    _settle_and_start(d)  # peak=61.5, threshold~=61.03
    d.update(humidity=58.0, now=t(200))  # below threshold -> COOLDOWN

    # Decline (61.5 - 58.0 = 3.5 >= 1.0) first observed here; not confirmed yet.
    result = d.update(humidity=58.0, now=t(210))
    assert result is None
    assert d.state is SessionState.COOLDOWN

    # 60s of continuous decline >= decline_confirm_seconds (60) -> ENDED,
    # well before cooldown_seconds (300) would have elapsed since t(200).
    change = d.update(humidity=58.0, now=t(270))
    assert change is not None
    assert change.event is SessionEvent.ENDED
    assert d.state is SessionState.IDLE


def test_single_noisy_dip_does_not_end_session_without_presence():
    """Without presence corroboration, a decline that doesn't hold for the
    full decline_confirm_seconds window does not end the session — this is
    the noise guard ADR-0005 requires for the no-presence path."""
    d = make_detector(start_delta=3.0, cooldown=300, decline_delta=1.0, decline_confirm=60)
    _settle_and_start(d)  # peak=61.5, threshold~=61.03
    d.update(humidity=58.0, now=t(200))  # -> COOLDOWN

    result = d.update(humidity=58.0, now=t(210))  # decline observed, timer starts
    assert result is None

    # Recovers to 60.8 — still below the RESUME threshold (~61.03), so stays
    # in COOLDOWN, but only 0.7pt off peak (< decline_delta) so the decline
    # is no longer considered to hold; its confirmation timer resets.
    result = d.update(humidity=60.8, now=t(220))
    assert result is None
    assert d.state is SessionState.COOLDOWN

    # Even after what would have been the full decline_confirm_seconds from
    # the *original* dip, the reset means it still hasn't ended.
    result = d.update(humidity=60.8, now=t(270))
    assert result is None
    assert d.state is SessionState.COOLDOWN


def test_decline_with_confirmed_presence_clear_ends_session_immediately():
    """With a presence sensor reading continuously False for
    presence_clear_confirm_seconds, a concurrent decline ends the session
    — using a decline_confirm_seconds long enough that, absent presence,
    it would NOT have fired yet, isolating the presence-gated fast path."""
    d = make_detector(
        start_delta=3.0,
        cooldown=300,
        decline_delta=1.0,
        decline_confirm=600,  # deliberately long — presence path must win
        presence_clear_confirm=60,
    )
    _settle_and_start(d)  # peak=61.5, threshold~=61.03
    d.update(humidity=58.0, now=t(200))  # -> COOLDOWN

    result = d.update(humidity=58.0, now=t(210), presence=False)
    assert result is None  # presence-clear just started accruing

    change = d.update(humidity=58.0, now=t(270), presence=False)
    assert change is not None
    assert change.event is SessionEvent.ENDED
    assert d.state is SessionState.IDLE


def test_presence_true_does_not_end_session_even_with_decline():
    """presence=True never contributes to the presence-clear confirmation,
    regardless of how long or how large the decline is."""
    d = make_detector(
        start_delta=3.0,
        cooldown=300,
        decline_delta=1.0,
        decline_confirm=600,
        presence_clear_confirm=60,
    )
    _settle_and_start(d)
    d.update(humidity=58.0, now=t(200))  # -> COOLDOWN

    result = d.update(humidity=58.0, now=t(280), presence=True)
    assert result is None
    assert d.state is SessionState.COOLDOWN


def test_flat_humidity_still_ends_via_cooldown_timeout_fallback():
    """Humidity that is flat (not declining enough) with no presence data
    still ends via the original elapsed-cooldown fallback — unchanged
    behavior for the 'stable' case."""
    d = make_detector(start_delta=3.0, cooldown=300, decline_delta=5.0, decline_confirm=60)
    _settle_and_start(d)  # peak=61.5
    d.update(humidity=58.0, now=t(200))  # -> COOLDOWN; decline only 3.5 < 5.0

    result = d.update(humidity=58.0, now=t(200 + 299))
    assert result is None
    assert d.state is SessionState.COOLDOWN

    change = d.update(humidity=58.0, now=t(200 + 300))
    assert change is not None
    assert change.event is SessionEvent.ENDED


def test_resume_resets_peak_and_decline_and_presence_tracking():
    """RESUMED resets peak_humidity to the raw resume reading, consistent
    with the existing active_since_humidity reset for sibling showers."""
    d = make_detector(start_delta=3.0, cooldown=300, decline_delta=1.0, decline_confirm=60)
    _settle_and_start(d)  # peak=61.5
    d.update(humidity=58.0, now=t(200))  # -> COOLDOWN
    d.update(humidity=58.0, now=t(210))  # decline timer starts accruing

    change = d.update(humidity=70.0, now=t(220))  # RESUMED (sibling), above threshold
    assert change.event is SessionEvent.RESUMED
    assert d.peak_humidity == 70.0

    # Dropping straight back below threshold measures decline against the
    # fresh peak (70.0), not the stale 61.5 — confirms the reset actually
    # took effect rather than just not having been exercised yet.
    d.update(humidity=58.0, now=t(230))  # -> COOLDOWN again, off the fresh peak
    assert d.state is SessionState.COOLDOWN
    result = d.update(humidity=58.0, now=t(240))  # decline timer starts (fresh peak)
    assert result is None
    change = d.update(humidity=58.0, now=t(240 + 60))
    assert change is not None
    assert change.event is SessionEvent.ENDED


# ---------------------------------------------------------------------------
# Presence-corroborated decline can end a session directly from ACTIVE
# (ADR-0006) — for a session whose humidity never drops back below the
# frozen absolute session_start_threshold, so it never reaches COOLDOWN
# under ADR-0005's logic alone.
# ---------------------------------------------------------------------------

def test_presence_corroborated_decline_ends_session_directly_from_active():
    """A session that peaks, then declines to a plateau still well above the
    frozen threshold (so it never enters COOLDOWN), ends directly from
    ACTIVE once presence has read continuously False for
    presence_clear_confirm_seconds alongside the decline."""
    d = make_detector(start_delta=3.0, decline_delta=1.0, presence_clear_confirm=60)
    _settle_and_start(d)  # peak=61.5, threshold~=61.03
    d.update(humidity=90.0, now=t(300))  # still ACTIVE, new peak=90.0

    # Declines to 65.0 — still well above the frozen threshold (~61.03), so
    # this never crosses into COOLDOWN under ADR-0005's logic alone.
    result = d.update(humidity=65.0, now=t(310), presence=False)
    assert result is None
    assert d.state is SessionState.ACTIVE  # confirms it never reached COOLDOWN

    # 60s of continuous presence-clear, decline still holding (25pts >= 1.0).
    change = d.update(humidity=65.0, now=t(370), presence=False)
    assert change is not None
    assert change.event is SessionEvent.ENDED
    assert d.state is SessionState.IDLE


def test_presence_true_does_not_end_session_from_active_even_with_decline():
    """presence=True never contributes to the ACTIVE-state fast path,
    regardless of how large the decline is."""
    d = make_detector(start_delta=3.0, decline_delta=1.0, presence_clear_confirm=60)
    _settle_and_start(d)
    d.update(humidity=90.0, now=t(300))

    d.update(humidity=65.0, now=t(310), presence=True)
    result = d.update(humidity=65.0, now=t(370), presence=True)
    assert result is None
    assert d.state is SessionState.ACTIVE


def test_decline_alone_does_not_end_session_from_active_without_presence():
    """Without presence corroboration, a sustained decline that never drops
    below the frozen threshold does NOT end the session from ACTIVE, even
    well past decline_confirm_seconds — that path deliberately stays
    COOLDOWN-only (see ADR-0006's 'Alternatives Considered')."""
    d = make_detector(start_delta=3.0, decline_delta=1.0, decline_confirm=60)
    _settle_and_start(d)
    d.update(humidity=90.0, now=t(300))

    result = d.update(humidity=65.0, now=t(310))  # no presence data at all
    assert result is None
    assert d.state is SessionState.ACTIVE

    result = d.update(humidity=65.0, now=t(1000))  # long past decline_confirm_seconds
    assert result is None
    assert d.state is SessionState.ACTIVE  # known limitation without a presence sensor


def test_regression_incident_stuck_active_session_now_ends():
    """Regression test for the real incident ADR-0006 fixes: a shower peaks
    high, declines to a plateau that never crosses back below the frozen
    absolute threshold (baseline settled at 50.0, humidity_start_delta=3.0
    -> threshold=53.0; the plateau here is 58-70%, matching the logged
    54-59% plateau), but presence clears shortly after the peak. The
    session must end via the ACTIVE-state presence-corroborated path
    instead of staying ACTIVE for the rest of the day."""
    d = make_detector(start_delta=3.0, decline_delta=1.0, presence_clear_confirm=60)
    d.update(humidity=50.0, now=t(0))
    d.update(humidity=50.0, now=t(700))  # baseline settles at 50.0
    change = d.update(humidity=54.6, now=t(705))  # STARTED, threshold=53.0
    assert change is not None and change.event is SessionEvent.STARTED

    d.update(humidity=84.4, now=t(900))  # peak, still ACTIVE

    # Declines to 70.0 — still well above the 53.0 threshold, so this never
    # crosses into COOLDOWN. Presence clears here too.
    result = d.update(humidity=70.0, now=t(1200), presence=False)
    assert result is None
    assert d.state is SessionState.ACTIVE  # confirms the pre-fix stuck state

    # 100s later: decline holds (84.4 -> 58.0 = 26.4pts) and presence has
    # been clear for 100s (>= presence_clear_confirm_seconds=60) -> ends
    # directly from ACTIVE, without ever reaching COOLDOWN.
    change = d.update(humidity=58.0, now=t(1300), presence=False)
    assert change is not None
    assert change.event is SessionEvent.ENDED
    assert d.state is SessionState.IDLE


# ---------------------------------------------------------------------------
# Baseline rebase at session end (ADR-0008) — a session ending while
# humidity is still well above true ambient (which ADR-0006 deliberately
# allows) must not leave a stale pre-shower baseline for the next session
# to inherit; without a rebase, a second person's shower starting shortly
# after could see an artificially inflated (or, less predictably,
# artificially small) delta depending on how long the first session ran.
# ---------------------------------------------------------------------------

def test_end_session_rebases_baseline_to_ending_humidity():
    """The instant a session ends, baseline_humidity snaps to that exact
    reading — not the stale pre-session ambient it was frozen at."""
    d = make_detector(start_delta=3.0, cooldown=300)
    d.update(humidity=50.0, now=t(0))
    d.update(humidity=50.0, now=t(700))  # baseline settles at 50.0
    d.update(humidity=54.0, now=t(705))  # STARTED (threshold ~53.0)
    change = d.update(humidity=50.0, now=t(1200))  # below threshold -> COOLDOWN
    assert change is None
    assert d.state is SessionState.COOLDOWN
    change = d.update(humidity=50.0, now=t(1200 + 300))  # cooldown timeout -> ENDED
    assert change is not None and change.event is SessionEvent.ENDED
    assert d.baseline_humidity == 50.0  # matches ending humidity exactly


def test_short_first_session_does_not_leave_stale_baseline_for_next_shower():
    """The worst case under the old (pre-ADR-0008) behavior: a SHORT first
    session, ending quickly, gave the EMA almost no time to catch the
    baseline up on its own (dt too small for meaningful alpha) — leaving
    the baseline stuck near the stale pre-shower ambient for whoever
    showers next. With the rebase, session duration is irrelevant: the
    baseline is exactly right immediately, regardless of how short the
    first session was."""
    d = make_detector(
        start_delta=3.0, decline_delta=1.0, presence_clear_confirm=60
    )
    d.update(humidity=50.0, now=t(0))
    d.update(humidity=50.0, now=t(700))  # baseline settles at 50.0
    d.update(humidity=54.0, now=t(705))  # STARTED
    d.update(humidity=90.0, now=t(710), presence=False)  # rises fast, peak=90.0, decline/presence-clear accrual starts

    # Ends via the ADR-0006 presence-corroborated path just 65 seconds after
    # starting — about as short a session as this path allows.
    change = d.update(humidity=85.0, now=t(775), presence=False)
    assert change is not None
    assert change.event is SessionEvent.ENDED

    # Under the old logic, the baseline (dt only ~65-70s vs tau=600s) would
    # have moved only a small fraction of the way from 50.0 toward 85.0.
    # Under ADR-0008, it's exactly 85.0 regardless of the short duration.
    assert d.baseline_humidity == 85.0


def test_second_shower_gets_accurate_delta_after_first_ends_at_high_humidity():
    """Regression test for the real-world concern this ADR fixes: a second
    person showering shortly after the first session ends (while humidity
    is still high) gets a delta baseline reflecting the ACTUAL current
    room condition, not the first person's original pre-shower ambient —
    so their own real humidity contribution is measured fairly, and a cut
    only fires once THEY have genuinely raised humidity by
    max_humidity_delta points above where the room actually was when they
    started, not an inherited, artificially large gap."""
    d = make_detector(start_delta=3.0, decline_delta=1.0, presence_clear_confirm=60)
    d.update(humidity=50.0, now=t(0))
    d.update(humidity=50.0, now=t(700))  # baseline settles at 50.0
    d.update(humidity=54.0, now=t(705))  # first session STARTED
    d.update(humidity=90.0, now=t(760), presence=False)  # peak, decline/clear accrual starts

    # First session ends via ADR-0006, humidity still very high (85.0).
    change = d.update(humidity=85.0, now=t(825), presence=False)  # +65s clear
    assert change is not None and change.event is SessionEvent.ENDED
    assert d.baseline_humidity == 85.0

    # A brief gap (residual humidity dissipating slightly) before the
    # second person's shower genuinely starts.
    d.update(humidity=82.0, now=t(900))  # still IDLE, baseline tracks down
    baseline_before_second_start = d.baseline_humidity

    # Second person's shower raises humidity well above wherever the
    # baseline has tracked to by now — a real, fresh rise.
    change = d.update(humidity=baseline_before_second_start + 5.0, now=t(910))
    assert change is not None and change.event is SessionEvent.STARTED
    # Their own delta baseline is the accurate recent reading (allowing for
    # the small EMA nudge from this very triggering reading), not the stale
    # 50.0 from hours earlier.
    assert abs(d.active_since_humidity - baseline_before_second_start) < 0.5
    assert d.active_since_humidity > 70.0  # nowhere near the stale 50.0


# ---------------------------------------------------------------------------
# active_since tracking
# ---------------------------------------------------------------------------

def test_active_since_none_when_idle():
    d = make_detector()
    assert d.active_since is None


def test_active_since_set_on_start():
    d = make_detector(start_delta=3.0)
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=62.0, now=t(5))
    assert d.active_since == t(5)


def test_active_since_cleared_on_end():
    d = make_detector(start_delta=3.0, cooldown=10)
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=62.0, now=t(5))
    d.update(humidity=40.0, now=t(10))
    d.update(humidity=40.0, now=t(20))
    assert d.active_since is None


# ---------------------------------------------------------------------------
# active_since_humidity tracking
# ---------------------------------------------------------------------------

def test_active_since_humidity_none_when_idle():
    d = make_detector()
    assert d.active_since_humidity is None


def test_active_since_humidity_cleared_on_end():
    d = make_detector(start_delta=3.0, cooldown=10)
    d.update(humidity=58.0, now=t(0))
    d.update(humidity=62.0, now=t(5))
    d.update(humidity=40.0, now=t(10))
    d.update(humidity=40.0, now=t(20))
    assert d.active_since_humidity is None


# ---------------------------------------------------------------------------
# StateChange dataclass
# ---------------------------------------------------------------------------

def test_state_change_str_is_readable():
    d = make_detector(start_delta=3.0)
    d.update(humidity=58.0, now=t(0))
    change = d.update(humidity=62.0, now=t(5))
    text = str(change)
    assert "STARTED" in text
    assert "idle" in text
    assert "active" in text
    assert "62.0" in text


if __name__ == "__main__":
    tests = [
        test_first_reading_seeds_baseline_and_never_starts_session,
        test_baseline_stays_put_under_stable_humidity,
        test_slow_ambient_drift_does_not_start_a_session,
        test_fast_rise_above_baseline_starts_session,
        test_baseline_humidity_property_exposes_current_value,
        test_ema_time_constant_controls_smoothing_speed,
        test_baseline_catches_up_after_a_session_ends,
        test_active_stays_active_while_above_frozen_threshold,
        test_active_enters_cooldown_when_dropping_below_frozen_threshold,
        test_cooldown_ends_session_after_elapsed,
        test_cooldown_resumes_on_rise_above_frozen_threshold,
        test_sibling_shower_gets_fresh_baseline_on_resume,
        test_peak_humidity_tracks_highest_reading_while_active,
        test_sustained_decline_ends_session_before_cooldown_elapses_no_presence,
        test_single_noisy_dip_does_not_end_session_without_presence,
        test_decline_with_confirmed_presence_clear_ends_session_immediately,
        test_presence_true_does_not_end_session_even_with_decline,
        test_flat_humidity_still_ends_via_cooldown_timeout_fallback,
        test_resume_resets_peak_and_decline_and_presence_tracking,
        test_presence_corroborated_decline_ends_session_directly_from_active,
        test_presence_true_does_not_end_session_from_active_even_with_decline,
        test_decline_alone_does_not_end_session_from_active_without_presence,
        test_regression_incident_stuck_active_session_now_ends,
        test_end_session_rebases_baseline_to_ending_humidity,
        test_short_first_session_does_not_leave_stale_baseline_for_next_shower,
        test_second_shower_gets_accurate_delta_after_first_ends_at_high_humidity,
        test_active_since_none_when_idle,
        test_active_since_set_on_start,
        test_active_since_cleared_on_end,
        test_active_since_humidity_none_when_idle,
        test_active_since_humidity_cleared_on_end,
        test_state_change_str_is_readable,
    ]
    passed = 0
    failed = 0
    for t_fn in tests:
        try:
            t_fn()
            print(f"PASS  {t_fn.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {t_fn.__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
