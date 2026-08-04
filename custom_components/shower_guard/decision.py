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

from .const import (
    DEFAULT_DECISION_LOG_SIZE,
    DEFAULT_MAX_HUMIDITY_DELTA,
    DEFAULT_PRESENCE_CONFIRMATION_WINDOW_SECONDS,
)
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

    Policies (checked in order; see ADR-0003):
    1. No active session -> water available.
    2. Humidity has risen by ``max_humidity_delta`` or more above the current
       baseline (session-start reading, reset on every RESUMED event — see
       ``SessionDetector.active_since_humidity`` — so a sibling starting a
       fresh shower during the cooldown window gets its own baseline rather
       than inheriting the previous person's cumulative rise) AND presence
       has been confirmed within ``presence_confirmation_window_seconds``
       (either ``presence`` is ``True`` right now, or it was seen ``True`` at
       ``last_presence_at`` within the window) -> cut water. Presence is a
       *confirmation gate*, not an independent trigger: exceeding the delta
       alone, without a recent presence confirmation, does NOT cut water —
       this guards against a stray humidity rise (weather, another steam
       source) being mistaken for an actual unattended shower. Without a
       presence sensor configured at all, ``presence`` and
       ``last_presence_at`` are always ``None``, so this policy can never
       cut — configure ``max_session_seconds`` (policy 3) as a fallback in
       that case.
    3. Optional duration fallback: if ``max_session_seconds`` is configured
       (not ``None``) and the session has run longer than it -> cut water.
       Disabled by default, and independent of presence — the safety net for
       a session that never gets a delta+presence match (e.g. no presence
       sensor, a cold shower that never trips the delta, or a delta that
       trips but is never presence-confirmed). Checked even when policy 2's
       delta condition was met but presence wasn't confirmed — that case
       doesn't return early.
    4. Otherwise -> water available.

    ``evaluate()`` is a pure computation. Callers are responsible for logging
    or acting on the result — this layer never calls an actuator or HA script
    (see ADR-0001).
    """

    def __init__(
        self,
        max_humidity_delta: float = DEFAULT_MAX_HUMIDITY_DELTA,
        max_session_seconds: Optional[float] = None,
        presence_confirmation_window_seconds: float = DEFAULT_PRESENCE_CONFIRMATION_WINDOW_SECONDS,
    ) -> None:
        self.max_humidity_delta = max_humidity_delta
        self.max_session_seconds = max_session_seconds
        self.presence_confirmation_window_seconds = presence_confirmation_window_seconds

    def evaluate(
        self,
        session_state: SessionState,
        active_since: Optional[datetime],
        now: datetime,
        humidity: Optional[float] = None,
        active_since_humidity: Optional[float] = None,
        presence: Optional[bool] = None,
        last_presence_at: Optional[datetime] = None,
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
                                    configured and its current state is known,
                                    otherwise ``None``.
            last_presence_at:       Timestamp presence was last seen ``True``,
                                    or ``None`` if never/no presence sensor.
                                    Used with ``presence`` to compute the
                                    confirmation window (see policy 2 above).
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

        delta = (
            humidity - active_since_humidity
            if humidity is not None and active_since_humidity is not None
            else None
        )

        if delta is not None and delta >= self.max_humidity_delta:
            presence_confirmed = presence is True or (
                last_presence_at is not None
                and (now - last_presence_at).total_seconds()
                <= self.presence_confirmation_window_seconds
            )
            if presence_confirmed:
                return DecisionResult(
                    decision=Decision.WATER_CUT,
                    reason=(
                        f"Humidity rose {delta:.1f} points since session start "
                        f"(>= {self.max_humidity_delta:.1f}) with presence "
                        f"confirmed within the last "
                        f"{self.presence_confirmation_window_seconds:.0f}s."
                    ),
                    timestamp=now,
                    session_state=session_state,
                    session_duration_seconds=duration,
                    humidity_delta=delta,
                )
            # Delta exceeded but not presence-confirmed: this policy alone
            # won't cut, but doesn't return early either — the duration
            # fallback below is an independent safety net and still gets a
            # chance to catch the session.
            delta_unconfirmed_reason = (
                f"Humidity rose {delta:.1f} points (>= "
                f"{self.max_humidity_delta:.1f}) but no presence confirmed "
                f"within the last "
                f"{self.presence_confirmation_window_seconds:.0f}s; not cutting."
            )
        else:
            delta_unconfirmed_reason = None

        if self.max_session_seconds is not None and duration >= self.max_session_seconds:
            return DecisionResult(
                decision=Decision.WATER_CUT,
                reason=(
                    f"Session exceeded max duration "
                    f"({duration:.0f}s >= {self.max_session_seconds:.0f}s)."
                ),
                timestamp=now,
                session_state=session_state,
                session_duration_seconds=duration,
                humidity_delta=delta,
            )

        return DecisionResult(
            decision=Decision.WATER_AVAILABLE,
            reason=delta_unconfirmed_reason or "Humidity rise within allowed delta.",
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
