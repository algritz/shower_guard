# ---
# purpose: Decision Engine layer — decides whether water should remain
#          available. Actuator-agnostic per ADR-0001. Also includes
#          DecisionLog, a bounded audit trail of evaluations (v0.4).
# version: 0.6.0
# note:    Pure Python. No Home Assistant imports. Consumes Session Detection
#          output (and, optionally, a presence reading) only — never raw
#          sensor data. Safe to unit-test and replay. Dry run only:
#          evaluations are computed and returned/logged by the caller — no
#          actuator or HA script is invoked here.
# ---

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from .const import DEFAULT_DECISION_LOG_SIZE, DEFAULT_MAX_SESSION_SECONDS
from .session import SessionState


class Decision(Enum):
    """Water availability decision."""
    WATER_AVAILABLE = "water_available"
    WATER_CUT = "water_cut"


@dataclass(frozen=True)
class DecisionResult:
    """Outcome of a single Decision Engine evaluation."""
    decision: Decision
    reason: str
    timestamp: datetime
    session_state: SessionState
    session_duration_seconds: float

    def __str__(self) -> str:
        return (
            f"[{self.timestamp.isoformat()}] "
            f"{self.decision.value.upper()} "
            f"(state={self.session_state.value}, "
            f"duration={self.session_duration_seconds:.0f}s) — {self.reason}"
        )


class DecisionEngine:
    """
    Decides whether water should remain available.

    Policies (checked in order):
    1. No active session -> water available.
    2. Presence configured and explicitly absent (``presence=False``) during
       an active session -> cut water immediately (unattended running
       shower). Ignored when ``presence`` is ``None`` (no presence sensor
       configured, or its state is unknown).
    3. Session running longer than ``max_session_seconds`` -> cut water.
    4. Otherwise -> water available.

    Dry run (v0.3): ``evaluate()`` is a pure computation. Callers are
    responsible for logging or acting on the result — this layer never calls
    an actuator or HA script (see ADR-0001; actuator wiring arrives in v1.0).
    """

    def __init__(
        self, max_session_seconds: float = DEFAULT_MAX_SESSION_SECONDS
    ) -> None:
        self.max_session_seconds = max_session_seconds

    def evaluate(
        self,
        session_state: SessionState,
        active_since: Optional[datetime],
        now: datetime,
        presence: Optional[bool] = None,
    ) -> DecisionResult:
        """
        Evaluate whether water should remain available.

        Args:
            session_state: Current Session Detection state.
            active_since:  Timestamp the current session began, or ``None``
                            if idle (see ``SessionDetector.active_since``).
            now:            Timestamp of this evaluation.
            presence:       ``True``/``False`` if a presence sensor is
                            configured and its state is known, otherwise
                            ``None`` (no presence sensor, or state unknown).
        """
        if session_state is SessionState.IDLE or active_since is None:
            return DecisionResult(
                decision=Decision.WATER_AVAILABLE,
                reason="No active session.",
                timestamp=now,
                session_state=session_state,
                session_duration_seconds=0.0,
            )

        duration = (now - active_since).total_seconds()

        if presence is False:
            return DecisionResult(
                decision=Decision.WATER_CUT,
                reason="No presence detected during an active session.",
                timestamp=now,
                session_state=session_state,
                session_duration_seconds=duration,
            )

        if duration >= self.max_session_seconds:
            return DecisionResult(
                decision=Decision.WATER_CUT,
                reason=(
                    f"Session exceeded max duration "
                    f"({duration:.0f}s >= {self.max_session_seconds:.0f}s)."
                ),
                timestamp=now,
                session_state=session_state,
                session_duration_seconds=duration,
            )

        return DecisionResult(
            decision=Decision.WATER_AVAILABLE,
            reason="Session within allowed duration.",
            timestamp=now,
            session_state=session_state,
            session_duration_seconds=duration,
        )


class DecisionLog:
    """
    Bounded, in-memory audit trail of Decision Engine evaluations (v0.4).

    Every ``DecisionEngine.evaluate()`` result can be recorded here, giving a
    structured history for troubleshooting today and a foundation the Replay
    Engine (v0.5) can build on. Oldest entries are dropped once ``max_entries``
    is exceeded. Pure Python — no Home Assistant imports.
    """

    def __init__(self, max_entries: int = DEFAULT_DECISION_LOG_SIZE) -> None:
        self.max_entries = max_entries
        self._entries: deque[DecisionResult] = deque(maxlen=max_entries)

    def record(self, result: DecisionResult) -> None:
        """Append a decision result to the log."""
        self._entries.append(result)

    @property
    def entries(self) -> tuple[DecisionResult, ...]:
        """All recorded entries, oldest first."""
        return tuple(self._entries)

    @property
    def last(self) -> Optional[DecisionResult]:
        """Most recently recorded entry, or ``None`` if empty."""
        return self._entries[-1] if self._entries else None

    def __len__(self) -> int:
        return len(self._entries)
