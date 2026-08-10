# ---
# purpose: Session Detection layer — determines when a shower session starts,
#          continues, resumes, or ends based on humidity readings, using a
#          dynamic ambient baseline instead of a flat absolute threshold, a
#          decline-from-peak trend (ADR-0005, extended to ACTIVE by ADR-0006)
#          optionally gated by presence, and (ADR-0008) a baseline rebase at
#          session end so a session starting shortly afterward doesn't
#          inherit a stale pre-shower ambient reading.
# version: 1.5.0 (integrated into repo v1.9.0, ADR-0008)
# note:    Pure Python. No Home Assistant imports. Safe to unit-test and
#          replay. ``presence`` is accepted as a plain Optional[bool] value
#          the wiring layer already tracks — this file never touches HA.
# ---

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from .const import (
    DEFAULT_BASELINE_TIME_CONSTANT_SECONDS,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DECLINE_CONFIRM_SECONDS,
    DEFAULT_HUMIDITY_DECLINE_DELTA,
    DEFAULT_HUMIDITY_START_DELTA,
    DEFAULT_PRESENCE_CLEAR_CONFIRM_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class SessionState(Enum):
    """Possible states of the shower session detector."""
    IDLE = "idle"           # No active session.
    ACTIVE = "active"       # Shower is running.
    COOLDOWN = "cooldown"   # Humidity dropped; waiting to confirm session ended.


class SessionEvent(Enum):
    """Events emitted when session state changes."""
    STARTED = "started"     # IDLE → ACTIVE
    RESUMED = "resumed"     # COOLDOWN → ACTIVE
    ENDED = "ended"         # COOLDOWN → IDLE


@dataclass(frozen=True)
class StateChange:
    """Describes a single session state transition."""
    event: SessionEvent
    previous: SessionState
    current: SessionState
    timestamp: datetime
    humidity: float

    def __str__(self) -> str:
        return (
            f"[{self.timestamp.isoformat()}] "
            f"{self.event.value.upper()} "
            f"({self.previous.value} → {self.current.value}, "
            f"humidity={self.humidity:.1f}%)"
        )


class SessionDetector:
    """
    Stateful humidity-based shower session detector.

    Feed humidity readings via ``update()``. Receive a ``StateChange`` whenever
    the session transitions, or ``None`` when the state is unchanged.

    Session start is relative, not absolute (v1.2): while IDLE, an ambient
    baseline is tracked via a time-based EMA (updated only in IDLE, frozen the
    instant a session starts). A session starts once humidity has risen
    ``humidity_start_delta`` points above that baseline — capturing the true
    starting point of a shower regardless of the room's ambient humidity that
    day. ``active_since_humidity`` is frozen at the baseline itself (not the
    raw crossing reading), so the Decision Engine's delta-cutoff policy sees
    the *entire* rise, not just the portion after an old flat floor (e.g.
    75%) happened to be crossed.

    State machine:

        IDLE ──[humidity - baseline >= start_delta]──► ACTIVE   (emit STARTED)
        ACTIVE ──[declined from peak AND presence confirmed clear]──► IDLE   (emit ENDED)
        ACTIVE ──[humidity < frozen session_start_threshold]──► COOLDOWN
        COOLDOWN ──[humidity >= frozen session_start_threshold]──► ACTIVE  (emit RESUMED)
        COOLDOWN ──[see below]──► IDLE                           (emit ENDED)

    ``session_start_threshold`` (``baseline + start_delta`` at the moment the
    session started) is frozen for the life of the session, giving the same
    hysteresis behavior the old flat threshold provided for ACTIVE/COOLDOWN
    transitions — only the *start* trigger is now baseline-relative.

    Session end (v1.3, ADR-0005; extended to ACTIVE by v1.4, ADR-0006) is no
    longer purely a fixed timer, nor gated solely on first crossing the
    frozen absolute threshold. Ending fires on the first of:

    1. **(ADR-0006, checked continuously from ACTIVE onward.)** Humidity has
       fallen ``humidity_decline_delta`` points from the session's peak, AND
       presence has read continuously ``False`` for at least
       ``presence_clear_confirm_seconds``. This can end a session directly
       from ACTIVE — without ever crossing the frozen absolute threshold —
       because presence confirms the room is empty regardless of how much
       residual humidity remains. Requires a presence sensor.
    2. **(ADR-0005, COOLDOWN only.)** The same decline has held continuously
       for at least ``decline_confirm_seconds``, with no presence data
       required. Deliberately restricted to COOLDOWN (i.e. only after
       humidity has already dropped below the frozen threshold) — without
       presence corroboration, a session still ACTIVE and above threshold
       must not be able to end just because humidity dipped briefly (e.g. a
       momentary temperature adjustment mid-shower).
    3. **(unchanged fallback.)** ``cooldown_seconds`` has elapsed since
       entering COOLDOWN — the "flat/stable humidity" case where no clear
       decline is detected.
    """

    def __init__(
        self,
        humidity_start_delta: float = DEFAULT_HUMIDITY_START_DELTA,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        baseline_time_constant_seconds: float = DEFAULT_BASELINE_TIME_CONSTANT_SECONDS,
        humidity_decline_delta: float = DEFAULT_HUMIDITY_DECLINE_DELTA,
        decline_confirm_seconds: float = DEFAULT_DECLINE_CONFIRM_SECONDS,
        presence_clear_confirm_seconds: float = DEFAULT_PRESENCE_CLEAR_CONFIRM_SECONDS,
    ) -> None:
        self.humidity_start_delta = humidity_start_delta
        self.cooldown_seconds = cooldown_seconds
        self.baseline_time_constant_seconds = baseline_time_constant_seconds
        self.humidity_decline_delta = humidity_decline_delta
        self.decline_confirm_seconds = decline_confirm_seconds
        self.presence_clear_confirm_seconds = presence_clear_confirm_seconds

        self._state: SessionState = SessionState.IDLE
        self._cooldown_start: Optional[datetime] = None
        self._active_since: Optional[datetime] = None
        self._active_since_humidity: Optional[float] = None

        self._baseline: Optional[float] = None
        self._baseline_updated_at: Optional[datetime] = None
        self._session_start_threshold: Optional[float] = None

        # ADR-0005: peak humidity since the session started (reset on
        # RESUMED, same as active_since_humidity), and the running "since"
        # timestamps used to confirm a sustained decline / presence-clear
        # before acting on either.
        self._peak_humidity: Optional[float] = None
        self._decline_since: Optional[datetime] = None
        self._presence_clear_since: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state(self) -> SessionState:
        """Current session state."""
        return self._state

    @property
    def active_since(self) -> Optional[datetime]:
        """
        Timestamp the current session began (set on STARTED, preserved through
        COOLDOWN/RESUMED), or ``None`` if IDLE. Used by the Decision Engine to
        compute session duration without duplicating Session Detection state.
        """
        return self._active_since

    @property
    def active_since_humidity(self) -> Optional[float]:
        """
        Humidity baseline used as the current session's starting point: set
        to the ambient baseline on STARTED, reset to the raw RESUMED reading
        (so a sibling starting a fresh shower during the cooldown window gets
        its own baseline), or ``None`` if IDLE. Used by the Decision Engine to
        compute humidity rise without duplicating Session Detection state.
        """
        return self._active_since_humidity

    @property
    def peak_humidity(self) -> Optional[float]:
        """
        Highest humidity reading seen since the current session started (set
        on STARTED, updated through ACTIVE, reset to the raw reading on
        RESUMED), or ``None`` if IDLE. The reference point ADR-0005's decline
        checks measure a drop from.
        """
        return self._peak_humidity

    @property
    def baseline_humidity(self) -> Optional[float]:
        """
        Current ambient baseline. Updates continuously while IDLE (time-based
        EMA) and is frozen for the duration of a session. ``None`` before the
        first reading has ever been seen.
        """
        return self._baseline

    def update(
        self, humidity: float, now: datetime, presence: Optional[bool] = None
    ) -> Optional[StateChange]:
        """
        Process a new humidity reading.

        Args:
            humidity: Current relative humidity (%).
            now:      Timestamp of the reading. Pass ``datetime.now()`` for live
                      use or a fixed value for replay/testing.
            presence: ``True``/``False`` if a presence sensor is configured
                      and its current state is known, otherwise ``None``
                      (no sensor configured, or state unknown/unavailable).
                      Consulted from ACTIVE onward (ADR-0006 extended this
                      from COOLDOWN-only) — a confirmed decline plus
                      continuously-clear presence can end a session
                      immediately, even before humidity has dropped below
                      the frozen absolute threshold.

        Returns:
            A ``StateChange`` if the session state changed, otherwise ``None``.
        """
        if self._state is SessionState.IDLE:
            self._update_baseline(humidity, now)
            above = (
                self._baseline is not None
                and (humidity - self._baseline) >= self.humidity_start_delta
            )
            return self._from_idle(above, humidity, now)

        if self._state is SessionState.ACTIVE:
            above = humidity >= self._session_start_threshold
            return self._from_active(above, humidity, now, presence)

        if self._state is SessionState.COOLDOWN:
            above = humidity >= self._session_start_threshold
            return self._from_cooldown(above, humidity, now, presence)

        return None  # unreachable

    # ------------------------------------------------------------------
    # Baseline tracking (IDLE only)
    # ------------------------------------------------------------------

    def _update_baseline(self, humidity: float, now: datetime) -> None:
        """
        Time-based EMA so irregular sensor push intervals don't distort the
        smoothing — a 2s gap and a 10-minute gap are weighted correctly.
        Not called while ACTIVE/COOLDOWN. Before ADR-0008, this meant the
        baseline stayed frozen at its pre-session value for the entire
        session and only caught up to current conditions via this EMA once
        IDLE resumed — a catch-up whose completeness depended on how long
        the session had run (a short session left the baseline barely
        moved). ADR-0008's rebase in ``_end_session`` now gives a session
        ending an accurate, immediate baseline, so this EMA's job is
        limited to what it's actually suited for: tracking gradual ambient
        drift while genuinely idle, not undoing a stale freeze.
        """
        if self._baseline is None:
            self._baseline = humidity
            self._baseline_updated_at = now
            return

        dt = max((now - self._baseline_updated_at).total_seconds(), 0.0)
        if dt == 0.0:
            return

        alpha = 1.0 - math.exp(-dt / self.baseline_time_constant_seconds)
        self._baseline += alpha * (humidity - self._baseline)
        self._baseline_updated_at = now

    # ------------------------------------------------------------------
    # Private state handlers
    # ------------------------------------------------------------------

    def _from_idle(
        self, above: bool, humidity: float, now: datetime
    ) -> Optional[StateChange]:
        if above:
            self._active_since = now
            # Freeze the baseline itself as the session's starting point —
            # not the raw reading that crossed the trigger — so the full
            # rise from true ambient conditions is visible downstream.
            self._active_since_humidity = self._baseline
            self._session_start_threshold = self._baseline + self.humidity_start_delta
            # ADR-0005: seed the peak at the triggering reading; it only
            # rises from here while ACTIVE.
            self._peak_humidity = humidity
            self._decline_since = None
            self._presence_clear_since = None
            return self._transition(SessionEvent.STARTED, SessionState.ACTIVE, humidity, now)
        return None

    def _update_decline_and_presence_tracking(
        self, humidity: float, now: datetime, presence: Optional[bool]
    ) -> tuple[bool, bool]:
        """
        Shared by ACTIVE (ADR-0006) and COOLDOWN (ADR-0005): update the
        continuous decline-from-peak and presence-clear timers against the
        current reading, and return ``(declined, presence_confirmed_clear)``.

        Tracking this identically in both states — rather than only once
        COOLDOWN is entered — is what lets ADR-0006's presence-corroborated
        path fire straight from ACTIVE: a session whose humidity never drops
        below the frozen absolute threshold (and so never reaches COOLDOWN
        under the old ADR-0005 logic) can still be recognized as declining.
        """
        declined = (
            self._peak_humidity is not None
            and (self._peak_humidity - humidity) >= self.humidity_decline_delta
        )
        if declined:
            if self._decline_since is None:
                self._decline_since = now
        else:
            self._decline_since = None

        if presence is False:
            if self._presence_clear_since is None:
                self._presence_clear_since = now
        else:
            # presence is True, or None (no sensor / unknown) — don't accrue.
            self._presence_clear_since = None
        presence_confirmed_clear = (
            presence is not None
            and self._presence_clear_since is not None
            and (now - self._presence_clear_since).total_seconds()
            >= self.presence_clear_confirm_seconds
        )

        return declined, presence_confirmed_clear

    def _end_session(self, humidity: float, now: datetime) -> StateChange:
        """Clear all session-scoped state and emit ENDED, from whichever
        state (ACTIVE or COOLDOWN) the end was confirmed in.

        ADR-0008: also rebases the ambient baseline to this exact reading —
        instead of leaving it frozen at its stale pre-session value to
        catch up unpredictably via ``_update_baseline``'s EMA on whatever
        reading happens to arrive next. Without this, a session ending
        while humidity is still well above true ambient (which ADR-0006
        deliberately allows) leaves a second person's shower starting
        shortly afterward measured against a baseline that's neither the
        old pre-shower ambient nor the current room condition — sometimes
        making their delta look artificially large (a stale-low baseline),
        sometimes artificially permissive (an EMA catch-up landing close to
        their own actual humidity). Rebasing here gives the same accuracy
        guarantee RESUMED already provides for a sibling shower still
        within the same COOLDOWN window, extended across the brief IDLE
        gap to a shower that starts just after full ENDED.
        """
        self._cooldown_start = None
        self._active_since = None
        self._active_since_humidity = None
        self._session_start_threshold = None
        self._peak_humidity = None
        self._decline_since = None
        self._presence_clear_since = None
        self._baseline = humidity
        self._baseline_updated_at = now
        return self._transition(SessionEvent.ENDED, SessionState.IDLE, humidity, now)

    def _from_active(
        self, above: bool, humidity: float, now: datetime, presence: Optional[bool]
    ) -> Optional[StateChange]:
        if self._peak_humidity is None or humidity > self._peak_humidity:
            self._peak_humidity = humidity

        declined, presence_confirmed_clear = self._update_decline_and_presence_tracking(
            humidity, now, presence
        )

        if declined and presence_confirmed_clear:
            # ADR-0006: presence conclusively confirms the room is empty, so
            # a corroborated decline ends the session immediately — even
            # though humidity hasn't dropped below the frozen absolute
            # session_start_threshold. Without this, a session can get stuck
            # ACTIVE indefinitely whenever post-shower residual humidity
            # settles above that threshold and never crosses it again (see
            # ADR-0006 for the incident this fixes). Deliberately requires
            # presence — decline alone is not trusted this early (see
            # _from_cooldown's decline_confirmed path, which stays
            # COOLDOWN-only for that reason).
            return self._end_session(humidity, now)

        if not above:
            # Enter cooldown; record when it started.
            self._cooldown_start = now
            self._state = SessionState.COOLDOWN
            self._decline_since = None
            self._presence_clear_since = None
            _LOGGER.debug(
                "Cooldown started at %s (humidity=%.1f%%)", now.isoformat(), humidity
            )
        return None  # no event on cooldown entry — session not yet confirmed ended

    def _from_cooldown(
        self, above: bool, humidity: float, now: datetime, presence: Optional[bool]
    ) -> Optional[StateChange]:
        if above:
            # Humidity recovered — session resumed. Reset the humidity
            # baseline (and ADR-0005/ADR-0006's peak/decline/presence
            # tracking) so a sibling starting a fresh shower during the
            # cooldown window gets its own baseline rather than inheriting
            # the previous person's cumulative humidity rise.
            self._cooldown_start = None
            self._active_since_humidity = humidity
            self._peak_humidity = humidity
            self._decline_since = None
            self._presence_clear_since = None
            return self._transition(SessionEvent.RESUMED, SessionState.ACTIVE, humidity, now)

        declined, presence_confirmed_clear = self._update_decline_and_presence_tracking(
            humidity, now, presence
        )
        decline_confirmed = (
            self._decline_since is not None
            and (now - self._decline_since).total_seconds() >= self.decline_confirm_seconds
        )

        if declined and presence_confirmed_clear:
            ended = True  # fastest path: decline corroborated by presence
        elif decline_confirmed:
            ended = True  # sustained decline alone, no presence needed —
            # safe here specifically because we're already below the frozen
            # absolute threshold (see ADR-0006 for why this path is not
            # also offered from ACTIVE).
        else:
            elapsed = (now - self._cooldown_start).total_seconds()
            ended = elapsed >= self.cooldown_seconds  # stable/timeout fallback

        if ended:
            return self._end_session(humidity, now)

        return None

    def _transition(
        self,
        event: SessionEvent,
        new_state: SessionState,
        humidity: float,
        now: datetime,
    ) -> StateChange:
        change = StateChange(
            event=event,
            previous=self._state,
            current=new_state,
            timestamp=now,
            humidity=humidity,
        )
        self._state = new_state
        _LOGGER.info("%s", change)
        return change
