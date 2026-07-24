# ---
# purpose: Decision Engine layer — decides whether water should remain
#          available. Actuator-agnostic per ADR-0001. Also includes
#          DecisionLog, a bounded audit trail of evaluations (v0.4).
# version: 1.1.0
# note:    Pure Python. No Home Assistant imports. Consumes Session Detection
#          output (current humidity, and optionally a presence reading)
#          only — never raw sensor state objects. Safe to unit-test and
#          replay. ``evaluate()`` is a pure computation — this layer never
#          calls an actuator or HA script itself (see ADR-0001; only the
#          wiring layer in __init__.py does that).
# ---

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from .const import DEFAULT_DECISION_LOG_SIZE, DEFAULT_MAX_HUMIDITY_DELTA
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
    humidity_delta: Optional[float] = None

    def __str__(self) -> str:
        delta_str = (
            f", delta={self.humidity_delta:.1f}pts"
            if self.humidity_delta is not None
            else ""
        )
        return (
            f"[{self.timestamp.isoformat()}] "
            f"{self.decision.value.upper()} "
            f"(state={self.session_state.value}, "
            f"duration={self.session_duration_seconds:.0f}s{delta_str}) — {self.reason}"
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
    3. Humidity has risen by ``max_humidity_delta`` or more above the current
       baseline -> cut water. The baseline is the session-start reading, and
       resets on every RESUMED event (see
       ``SessionDetector.active_since_humidity``) so a sibling starting a
       fresh shower during the cooldown window gets its own baseline rather
       than inheriting the previous person's cumulative rise. A hot shower
       generates steam quickly and gets capped sooner; a cold shower (little
       humidity rise) is not penalized just for running long. Skipped when
       ``humidity`` or ``active_since_humidity`` is unavailable — in that
       case the session has no time- or humidity-based cutoff.
    4. Otherwise -> water available.

    ``evaluate()`` is a pure computation. Callers are responsible for logging
    or acting on the result — this layer never calls an actuator or HA script
    (see ADR-0001).
    """

    def __init__(
        self, max_humidity_delta: float = DEFAULT_MAX_HUMIDITY_DELTA
    ) -> None:
        self.max_humidity_delta = max_humidity_delta

    def evaluate(
        self,
        session_state: SessionState,
        active_since: Optional[datetime],
        now: datetime,
        humidity: Optional[float] = None,
        active_since_humidity: Optional[float] = None,
        presence: Optional[bool] = None,
    ) -> DecisionResult:
        """
        Evaluate whether water should remain available.

        Args:
            session_state:          Current Session Detection state.
            active_since:           Timestamp the current session began, or
                                    ``None`` if idle (see
                                    ``SessionDetector.active_since``). Used
                                    only to compute ``session_duration_seconds``
                                    for observability — it is not a cutoff
                                    trigger.
            now:                    Timestamp of this evaluation.
            humidity:               Current humidity reading (% RH), if
                                    known.
            active_since_humidity:  Current baseline reading (see
                                    ``SessionDetector.active_since_humidity``),
                                    if known.
            presence:               ``True``/``False`` if a presence sensor is
                                    configured and its state is known,
                                    otherwise ``None``.
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

        delta = (
            humidity - active_since_humidity
            if humidity is not None and active_since_humidity is not None
            else None
        )

        if delta is not None and delta >= self.max_humidity_delta:
            return DecisionResult(
                decision=Decision.WATER_CUT,
                reason=(
                    f"Humidity rose {delta:.1f} points since session start "
                    f"(>= {self.max_humidity_delta:.1f})."
                ),
                timestamp=now,
                session_state=session_state,
                session_duration_seconds=duration,
                humidity_delta=delta,
            )

        return DecisionResult(
            decision=Decision.WATER_AVAILABLE,
            reason="Humidity rise within allowed delta.",
            timestamp=now,
            session_state=session_state,
            session_duration_seconds=duration,
            humidity_delta=delta,
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
