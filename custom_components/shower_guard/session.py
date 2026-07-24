# ---
# purpose: Session Detection layer — determines when a shower session starts,
#          continues, resumes, or ends based on humidity readings.
# version: 0.3.0
# note:    Pure Python. No Home Assistant imports. Safe to unit-test and replay.
# ---

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from .const import DEFAULT_COOLDOWN_SECONDS, DEFAULT_HUMIDITY_THRESHOLD

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

    State machine:

        IDLE ──[humidity >= threshold]──► ACTIVE
        ACTIVE ──[humidity < threshold]──► COOLDOWN
        COOLDOWN ──[humidity >= threshold]──► ACTIVE  (emit RESUMED)
        COOLDOWN ──[cooldown elapsed]──► IDLE          (emit ENDED)
    """

    def __init__(
        self,
        humidity_threshold: float = DEFAULT_HUMIDITY_THRESHOLD,
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    ) -> None:
        self.humidity_threshold = humidity_threshold
        self.cooldown_seconds = cooldown_seconds

        self._state: SessionState = SessionState.IDLE
        self._cooldown_start: Optional[datetime] = None
        self._active_since: Optional[datetime] = None

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
        above = humidity >= self.humidity_threshold

        if self._state is SessionState.IDLE:
            return self._from_idle(above, humidity, now)

        if self._state is SessionState.ACTIVE:
            return self._from_active(above, humidity, now)

        if self._state is SessionState.COOLDOWN:
            return self._from_cooldown(above, humidity, now)

        return None  # unreachable

    # ------------------------------------------------------------------
    # Private state handlers
    # ------------------------------------------------------------------

    def _from_idle(
        self, above: bool, humidity: float, now: datetime
    ) -> Optional[StateChange]:
        if above:
            self._active_since = now
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
            # Humidity recovered — session resumed.
            self._cooldown_start = None
            return self._transition(SessionEvent.RESUMED, SessionState.ACTIVE, humidity, now)

        elapsed = (now - self._cooldown_start).total_seconds()
        if elapsed >= self.cooldown_seconds:
            self._cooldown_start = None
            self._active_since = None
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
