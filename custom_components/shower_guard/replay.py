# ---
# purpose: Replay Engine — replays historical or synthetic humidity (and
#          optionally presence) readings through the exact same Session
#          Detection and Decision Engine classes used in production. No
#          decision logic is duplicated here.
# version: 1.5.0
# note:    Pure Python. No Home Assistant imports. Keep lightweight per
#          ADR-0001. Runnable standalone:
#          `python -m custom_components.shower_guard.replay <csv-file>`.
# ---

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

from .const import (
    DEFAULT_BASELINE_TIME_CONSTANT_SECONDS,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DECISION_LOG_SIZE,
    DEFAULT_DECLINE_CONFIRM_SECONDS,
    DEFAULT_HUMIDITY_DECLINE_DELTA,
    DEFAULT_HUMIDITY_START_DELTA,
    DEFAULT_MAX_HUMIDITY_DELTA,
    DEFAULT_PRESENCE_CLEAR_CONFIRM_SECONDS,
    DEFAULT_PRESENCE_CONFIRMATION_WINDOW_SECONDS,
)
from .decision import DecisionEngine, DecisionLog
from .session import SessionDetector, StateChange

Reading = Tuple[datetime, float]
PresenceReading = Tuple[datetime, bool]


@dataclass
class ReplayResult:
    """Output of a replay run — the same artifacts production would produce."""
    state_changes: List[StateChange] = field(default_factory=list)
    decision_log: DecisionLog = field(default_factory=DecisionLog)


def replay(
    readings: Iterable[Reading],
    *,
    humidity_start_delta: float = DEFAULT_HUMIDITY_START_DELTA,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    baseline_time_constant_seconds: float = DEFAULT_BASELINE_TIME_CONSTANT_SECONDS,
    humidity_decline_delta: float = DEFAULT_HUMIDITY_DECLINE_DELTA,
    decline_confirm_seconds: float = DEFAULT_DECLINE_CONFIRM_SECONDS,
    presence_clear_confirm_seconds: float = DEFAULT_PRESENCE_CLEAR_CONFIRM_SECONDS,
    max_humidity_delta: float = DEFAULT_MAX_HUMIDITY_DELTA,
    max_session_seconds: Optional[float] = None,
    presence_confirmation_window_seconds: float = DEFAULT_PRESENCE_CONFIRMATION_WINDOW_SECONDS,
    presence_readings: Optional[Iterable[PresenceReading]] = None,
    decision_log_size: int = DEFAULT_DECISION_LOG_SIZE,
) -> ReplayResult:
    """
    Replay a sequence of ``(timestamp, humidity)`` readings, oldest first,
    through the exact same ``SessionDetector`` and ``DecisionEngine`` classes
    used in production (see ``__init__.py``). This is orchestration only — no
    session or decision logic is reimplemented here. ``max_session_seconds``
    mirrors the optional duration fallback (disabled by default, ``None``).

    ``presence_readings``, if given, is an oldest-first sequence of
    ``(timestamp, presence)`` events mirroring a presence sensor's state
    changes, advanced in lockstep with ``readings`` so each evaluation sees
    the current presence and last-confirmed-True timestamp exactly as the
    live wiring computes them (see ADR-0003). Without it, delta-triggered
    cuts never confirm — matching production behavior with no
    ``presence_sensor`` configured. Both sequences must independently be
    sorted oldest-first; they don't need matching timestamps or lengths.
    The current presence value is also fed into ``SessionDetector.update()``
    on every reading (see ADR-0005), so a presence-confirmed session end can
    be exercised in replay the same way it fires in production.
    """
    detector = SessionDetector(
        humidity_start_delta=humidity_start_delta,
        cooldown_seconds=cooldown_seconds,
        baseline_time_constant_seconds=baseline_time_constant_seconds,
        humidity_decline_delta=humidity_decline_delta,
        decline_confirm_seconds=decline_confirm_seconds,
        presence_clear_confirm_seconds=presence_clear_confirm_seconds,
    )
    engine = DecisionEngine(
        max_humidity_delta=max_humidity_delta,
        max_session_seconds=max_session_seconds,
        presence_confirmation_window_seconds=presence_confirmation_window_seconds,
    )
    result = ReplayResult(decision_log=DecisionLog(max_entries=decision_log_size))

    presence_events = sorted(presence_readings or [], key=lambda r: r[0])
    presence_idx = 0
    current_presence: Optional[bool] = None
    last_presence_at: Optional[datetime] = None

    for now, humidity in readings:
        # Apply any presence events that occurred at or before this reading,
        # in order — mirrors the live presence callback updating
        # last_presence_at only when presence is confirmed True.
        while presence_idx < len(presence_events) and presence_events[presence_idx][0] <= now:
            event_time, current_presence = presence_events[presence_idx]
            if current_presence is True:
                last_presence_at = event_time
            presence_idx += 1

        change = detector.update(humidity=humidity, now=now, presence=current_presence)
        if change is not None:
            result.state_changes.append(change)

        decision = engine.evaluate(
            detector.state,
            detector.active_since,
            now,
            humidity=humidity,
            active_since_humidity=detector.active_since_humidity,
            presence=current_presence,
            last_presence_at=last_presence_at,
        )
        result.decision_log.record(decision)

    return result


def load_readings_from_csv(path: str) -> List[Reading]:
    """
    Load ``(timestamp, humidity)`` readings from a CSV file with ``timestamp``
    and ``humidity`` columns. Timestamps must be ISO 8601
    (parsed via ``datetime.fromisoformat``).
    """
    readings: List[Reading] = []
    with open(path, newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            readings.append(
                (datetime.fromisoformat(row["timestamp"]), float(row["humidity"]))
            )
    return readings


def load_presence_readings_from_csv(path: str) -> List[PresenceReading]:
    """
    Load ``(timestamp, presence)`` readings from a CSV file with
    ``timestamp`` and ``presence`` columns. ``presence`` must be ``on``/``off``
    (matching a real presence binary_sensor's states) or ``true``/``false``.
    """
    readings: List[PresenceReading] = []
    with open(path, newline="") as csv_file:
        for row in csv.DictReader(csv_file):
            presence_value = row["presence"].strip().lower()
            if presence_value not in ("on", "off", "true", "false"):
                raise ValueError(
                    f"Unrecognized presence value '{row['presence']}' in {path} "
                    "(expected on/off or true/false)"
                )
            readings.append(
                (
                    datetime.fromisoformat(row["timestamp"]),
                    presence_value in ("on", "true"),
                )
            )
    return readings


def _main() -> None:
    """CLI: replay a CSV file of readings and print the results."""
    parser = argparse.ArgumentParser(
        description=(
            "Replay recorded humidity (and optionally presence) readings "
            "through Shower Guard's Session Detection and Decision Engine "
            "(dry run — no actuator is called)."
        )
    )
    parser.add_argument("csv_file", help="CSV file with 'timestamp,humidity' columns")
    parser.add_argument(
        "--presence-csv",
        default=None,
        help="Optional CSV file with 'timestamp,presence' columns (on/off).",
    )
    parser.add_argument(
        "--humidity-start-delta", type=float, default=DEFAULT_HUMIDITY_START_DELTA
    )
    parser.add_argument(
        "--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS
    )
    parser.add_argument(
        "--baseline-time-constant-seconds",
        type=float,
        default=DEFAULT_BASELINE_TIME_CONSTANT_SECONDS,
    )
    parser.add_argument(
        "--humidity-decline-delta", type=float, default=DEFAULT_HUMIDITY_DECLINE_DELTA
    )
    parser.add_argument(
        "--decline-confirm-seconds",
        type=float,
        default=DEFAULT_DECLINE_CONFIRM_SECONDS,
    )
    parser.add_argument(
        "--presence-clear-confirm-seconds",
        type=float,
        default=DEFAULT_PRESENCE_CLEAR_CONFIRM_SECONDS,
    )
    parser.add_argument(
        "--max-humidity-delta", type=float, default=DEFAULT_MAX_HUMIDITY_DELTA
    )
    parser.add_argument(
        "--max-session-seconds",
        type=float,
        default=None,
        help="Optional duration fallback (disabled by default).",
    )
    parser.add_argument(
        "--presence-confirmation-window-seconds",
        type=float,
        default=DEFAULT_PRESENCE_CONFIRMATION_WINDOW_SECONDS,
    )
    args = parser.parse_args()

    readings = load_readings_from_csv(args.csv_file)
    presence_readings = (
        load_presence_readings_from_csv(args.presence_csv) if args.presence_csv else None
    )
    result = replay(
        readings,
        humidity_start_delta=args.humidity_start_delta,
        cooldown_seconds=args.cooldown_seconds,
        baseline_time_constant_seconds=args.baseline_time_constant_seconds,
        humidity_decline_delta=args.humidity_decline_delta,
        decline_confirm_seconds=args.decline_confirm_seconds,
        presence_clear_confirm_seconds=args.presence_clear_confirm_seconds,
        max_humidity_delta=args.max_humidity_delta,
        max_session_seconds=args.max_session_seconds,
        presence_confirmation_window_seconds=args.presence_confirmation_window_seconds,
        presence_readings=presence_readings,
    )

    print(f"Replayed {len(readings)} readings" + (
        f" and {len(presence_readings)} presence events.\n" if presence_readings else ".\n"
    ))

    print("Session state changes:")
    for change in result.state_changes:
        print(f"  {change}")

    print(f"\nDecision history ({len(result.decision_log)} entries, most recent 10):")
    for decision in result.decision_log.entries[-10:]:
        print(f"  {decision}")


if __name__ == "__main__":
    _main()
