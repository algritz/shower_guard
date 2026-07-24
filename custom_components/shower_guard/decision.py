# ---
# purpose: Decision Engine layer — decides whether water should remain
#          available. Actuator-agnostic per ADR-0001.
# version: 0.3.0
# note:    Pure Python. No Home Assistant imports. Consumes Session Detection
#          output only, never raw sensor data. Safe to unit-test and replay.
#          Dry run only (v0.3): evaluations are computed and returned/logged
#          by the caller — no actuator or HA script is invoked here.
# ---

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from .const import DEFAULT_MAX_SESSION_SECONDS
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

    Policy: cut water once the current session has been running longer than
    ``max_session_seconds``. Reinstate availability once the session ends.

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
    ) -> DecisionResult:
        """
        Evaluate whether water should remain available.

        Args:
            session_state: Current Session Detection state.
            active_since:  Timestamp the current session began, or ``None``
                            if idle (see ``SessionDetector.active_since``).
            now:            Timestamp of this evaluation.
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
