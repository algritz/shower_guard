from datetime import datetime
import sys
from pathlib import Path

# Ensure the repository root is on sys.path so `custom_components` imports work
repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root))

import importlib.util
import types

# Prepare package modules so relative imports in replay.py resolve.
cc_pkg_dir = repo_root / "custom_components"
sg_pkg_dir = cc_pkg_dir / "shower_guard"

custom_pkg = types.ModuleType("custom_components")
custom_pkg.__path__ = [str(cc_pkg_dir)]
sys.modules["custom_components"] = custom_pkg

sg_pkg = types.ModuleType("custom_components.shower_guard")
sg_pkg.__path__ = [str(sg_pkg_dir)]
sys.modules["custom_components.shower_guard"] = sg_pkg

# Load the replay module as part of the package so relative imports work.
replay_path = sg_pkg_dir / "replay.py"
spec = importlib.util.spec_from_file_location("custom_components.shower_guard.replay", replay_path)
replay = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = replay
spec.loader.exec_module(replay)  # type: ignore


def parse_iso_ts(s: str) -> datetime:
    # Accept trailing Z as UTC
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)


def main():
    path = str(repo_root / "test_data" / "readings.csv")
    readings = []
    with open(path, newline="") as f:
        import csv

        for row in csv.DictReader(f):
            readings.append((parse_iso_ts(row["timestamp"]), float(row["humidity"])))

    # Configuration values (from user's shower_guard.yaml)
    humidity_start_delta = 3.0
    cooldown_seconds = 300
    max_humidity_delta = 15.0
    max_session_seconds = None  # presence_sensor configured -> duration fallback disabled

    result = replay.replay(
        readings,
        humidity_start_delta=humidity_start_delta,
        cooldown_seconds=cooldown_seconds,
        max_humidity_delta=max_humidity_delta,
        max_session_seconds=max_session_seconds,
    )

    print(f"Session changes: {len(result.state_changes)}")
    for c in result.state_changes:
        print(f"  {c}")

    # Simulate wiring: configured script entity ids from user's config
    water_cut_script = "script.shower_guard_cut_water"
    water_available_script = "script.shower_guard_restore_water"

    print(f"\nDecision log entries: {len(result.decision_log)}")
    previous_decision = None
    for d in result.decision_log.entries:
        print(f"  {d}")
        if previous_decision is None or d.decision != previous_decision:
            # Decision changed — wiring would call the configured script (if set)
            script_entity = (
                water_cut_script if d.decision.name == "WATER_CUT" else water_available_script
            )
            if script_entity:
                print(
                    f"    -> Simulated actuator call: domain=script service=turn_on service_data={{'entity_id': '{script_entity}'}}"
                )
        previous_decision = d.decision


if __name__ == "__main__":
    main()
