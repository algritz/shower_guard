# ---
# purpose: Replay Engine — replays historical or synthetic humidity readings
#          through the exact same Session Detection and Decision Engine
#          classes used in production. No decision logic is duplicated here.
# version: 0.5.0
# note:    Pure Python. No Home Assistant imports. Keep lightweight per
#          ADR-0001. Runnable standalone:
#          `python -m custom_components.shower_guard.replay <csv-file>`.
# ---

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, List, Tuple

from .const import (
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_DECISION_LOG_SIZE,
    DEFAULT_HUMIDITY_THRESHOLD,
    DEFAULT_MAX_SESSION_SECONDS,
)
from .decision import DecisionEngine, DecisionLog
from .session import SessionDetector, StateChange

Reading = Tuple[datetime, float]


@dataclass
class ReplayResult:
    """Output of a replay run — the same artifacts production would produce."""
    state_changes: List[StateChange] = field(default_factory=list)
    decision_log: DecisionLog = field(default_factory=DecisionLog)


def replay(
    readings: Iterable[Reading],
    *,
    humidity_threshold: float = DEFAULT_HUMIDITY_THRESHOLD,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    max_session_seconds: float = DEFAULT_MAX_SESSION_SECONDS,
    decision_log_size: int = DEFAULT_DECISION_LOG_SIZE,
) -> ReplayResult:
    """
    Replay a sequence of ``(timestamp, humidity)`` readings, oldest first,
    through the exact same ``SessionDetector`` and ``DecisionEngine`` classes
    used in production (see ``__init__.py``). This is orchestration only — no
    session or decision logic is reimplemented here.
    """
    detector = SessionDetector(
        humidity_threshold=humidity_threshold, cooldown_seconds=cooldown_seconds
    )
    engine = DecisionEngine(max_session_seconds=max_session_seconds)
    result = ReplayResult(decision_log=DecisionLog(max_entries=decision_log_size))

    for now, humidity in readings:
        change = detector.update(humidity=humidity, now=now)
        if change is not None:
            result.state_changes.append(change)

        decision = engine.evaluate(detector.state, detector.active_since, now)
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


def _main() -> None:
    """CLI: replay a CSV file of readings and print the results."""
    parser = argparse.ArgumentParser(
        description=(
            "Replay recorded humidity readings through Shower Guard's Session "
            "Detection and Decision Engine (dry run — no actuator is called)."
        )
    )
    parser.add_argument("csv_file", help="CSV file with 'timestamp,humidity' columns")
    parser.add_argument(
        "--humidity-threshold", type=float, default=DEFAULT_HUMIDITY_THRESHOLD
    )
    parser.add_argument(
        "--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS
    )
    parser.add_argument(
        "--max-session-seconds", type=float, default=DEFAULT_MAX_SESSION_SECONDS
    )
    args = parser.parse_args()

    readings = load_readings_from_csv(args.csv_file)
    result = replay(
        readings,
        humidity_threshold=args.humidity_threshold,
        cooldown_seconds=args.cooldown_seconds,
        max_session_seconds=args.max_session_seconds,
    )

    print(f"Replayed {len(readings)} readings.\n")

    print("Session state changes:")
    for change in result.state_changes:
        print(f"  {change}")

    print(f"\nDecision history ({len(result.decision_log)} entries, most recent 10):")
    for decision in result.decision_log.entries[-10:]:
        print(f"  {decision}")


if __name__ == "__main__":
    _main()
