# ---
# purpose: Session Detection layer — determines when a shower session starts,
#          continues, resumes, or ends based on humidity readings, using a
#          dynamic ambient baseline instead of a flat absolute threshold, and
#          (as of ADR-0005) a decline-from-peak trend — optionally gated by
#          presence — instead of relying solely on an elapsed cooldown timer
#          to confirm the session actually ended.
# version: 1.3.0 (integrated into repo v1.6.0, ADR-0005)
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
        ACTIVE ──[humidity < frozen session_start_threshold]──► COOLDOWN
        COOLDOWN ──[humidity >= frozen session_start_threshold]──► ACTIVE  (emit RESUMED)
        COOLDOWN ──[see below]──► IDLE                           (emit ENDED)

    ``session_start_threshold`` (``baseline + start_delta`` at the moment the
    session started) is frozen for the life of the session, giving the same
    hysteresis behavior the old flat threshold provided for ACTIVE/COOLDOWN
    transitions — only the *start* trigger is now baseline-relative.

    Session end (v1.3, ADR-0005) is no longer purely a fixed timer. While in
    COOLDOWN, ending fires on the first of:

    1. Humidity has fallen ``humidity_decline_delta`` points from the
       session's peak, AND presence has read continuously ``False`` for at
       least ``presence_clear_confirm_seconds`` (fastest path — requires a
       presence sensor).
    2. The same decline has held continuously for at least
       ``decline_confirm_seconds``, with no presence data required.
    3. ``cooldown_seconds`` has elapsed since entering COOLDOWN (unchanged
       fallback — the "flat/stable humidity" case where no clear decline is
       detected).
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
                      Only consulted while in COOLDOWN (ADR-0005) — a
                      confirmed decline plus continuously-clear presence can
                      end a session faster than the cooldown timer alone.

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
            return self._from_active(above, humidity, now)

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
        Not called while ACTIVE/COOLDOWN, so the baseline resumes tracking
        (and naturally catches back up over time) once a session ends,
        rather than needing an explicit reset.
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

    def _from_active(
        self, above: bool, humidity: float, now: datetime
    ) -> Optional[StateChange]:
        if self._peak_humidity is None or humidity > self._peak_humidity:
            self._peak_humidity = humidity
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
            # baseline (and ADR-0005's peak/decline/presence tracking) so a
            # sibling starting a fresh shower during the cooldown window
            # gets its own baseline rather than inheriting the previous
            # person's cumulative humidity rise.
            self._cooldown_start = None
            self._active_since_humidity = humidity
            self._peak_humidity = humidity
            self._decline_since = None
            self._presence_clear_since = None
            return self._transition(SessionEvent.RESUMED, SessionState.ACTIVE, humidity, now)

        # --- ADR-0005: has humidity sustained a decline from the peak? ---
        declined = (
            self._peak_humidity is not None
            and (self._peak_humidity - humidity) >= self.humidity_decline_delta
        )
        if declined:
            if self._decline_since is None:
                self._decline_since = now
        else:
            self._decline_since = None
        decline_confirmed = (
            self._decline_since is not None
            and (now - self._decline_since).total_seconds() >= self.decline_confirm_seconds
        )

        # --- ADR-0005: has presence read continuously clear? ---
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

        if declined and presence_confirmed_clear:
            ended = True  # fastest path: decline corroborated by presence
        elif decline_confirmed:
            ended = True  # sustained decline alone, no presence needed
        else:
            elapsed = (now - self._cooldown_start).total_seconds()
            ended = elapsed >= self.cooldown_seconds  # stable/timeout fallback

        if ended:
            self._cooldown_start = None
            self._active_since = None
            self._active_since_humidity = None
            self._session_start_threshold = None
            self._peak_humidity = None
            self._decline_since = None
            self._presence_clear_since = None
            return self._transition(SessionEvent.ENDED, SessionState.IDLE, humidity, now)

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
