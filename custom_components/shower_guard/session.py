# ---
# purpose: Session Detection layer — determines when a shower session starts,
#          continues, resumes, or ends based on humidity readings, using a
#          dynamic ambient baseline instead of a flat absolute threshold.
# version: 1.2.0 (integrated into repo v1.5.0, ADR-0004)
# note:    Pure Python. No Home Assistant imports. Safe to unit-test and replay.
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
    DEFAULT_HUMIDITY_START_DELTA,
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
        COOLDOWN ──[cooldown elapsed]──► IDLE                    (emit ENDED)

    ``session_start_threshold`` (``baseline + start_delta`` at the moment the
    session started) is frozen for the life of the session, giving the same
    hysteresis behavior the old flat threshold provided for ACTIVE/COOLDOWN
    transitions — only the *start* trigger is now baseline-relative.
    """

    def __init__(
        self,
        humidity_start_delta: float = DEFAULT_HUMIDITY_START_DELTA,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        baseline_time_constant_seconds: float = DEFAULT_BASELINE_TIME_CONSTANT_SECONDS,
    ) -> None:
        self.humidity_start_delta = humidity_start_delta
        self.cooldown_seconds = cooldown_seconds
        self.baseline_time_constant_seconds = baseline_time_constant_seconds

        self._state: SessionState = SessionState.IDLE
        self._cooldown_start: Optional[datetime] = None
        self._active_since: Optional[datetime] = None
        self._active_since_humidity: Optional[float] = None

        self._baseline: Optional[float] = None
        self._baseline_updated_at: Optional[datetime] = None
        self._session_start_threshold: Optional[float] = None

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
    def baseline_humidity(self) -> Optional[float]:
        """
        Current ambient baseline. Updates continuously while IDLE (time-based
        EMA) and is frozen for the duration of a session. ``None`` before the
        first reading has ever been seen.
        """
        return self._baseline

    def update(self, humidity: float, now: datetime) -> Optional[StateChange]:
        """
        Process a new humidity reading.

        Args:
            humidity: Current relative humidity (%).
            now:      Timestamp of the reading. Pass ``datetime.now()`` for live
                      use or a fixed value for replay/testing.

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
            return self._from_cooldown(above, humidity, now)

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
            return self._transition(SessionEvent.STARTED, SessionState.ACTIVE, humidity, now)
        return None

    def _from_active(
        self, above: bool, humidity: float, now: datetime
    ) -> Optional[StateChange]:
        if not above:
            # Enter cooldown; record when it started.
            self._cooldown_start = now
            self._state = SessionState.COOLDOWN
            _LOGGER.debug(
                "Cooldown started at %s (humidity=%.1f%%)", now.isoformat(), humidity
            )
        return None  # no event on cooldown entry — session not yet confirmed ended

    def _from_cooldown(
        self, above: bool, humidity: float, now: datetime
    ) -> Optional[StateChange]:
        if above:
            # Humidity recovered — session resumed. Reset the humidity
            # baseline so a sibling starting a fresh shower during the
            # cooldown window gets its own baseline rather than inheriting
            # the previous person's cumulative humidity rise.
            self._cooldown_start = None
            self._active_since_humidity = humidity
            return self._transition(SessionEvent.RESUMED, SessionState.ACTIVE, humidity, now)

        elapsed = (now - self._cooldown_start).total_seconds()
        if elapsed >= self.cooldown_seconds:
            self._cooldown_start = None
            self._active_since = None
            self._active_since_humidity = None
            self._session_start_threshold = None
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
