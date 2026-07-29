# Graph Report - /home/biersh/Documents/git/shower_guard  (2026-07-29)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 238 nodes · 483 edges · 10 communities (9 shown, 1 thin omitted)
- Extraction: 82% EXTRACTED · 18% INFERRED · 0% AMBIGUOUS · INFERRED: 88 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `fdfdac6c`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_init.py
- test_session.py
- DecisionLog
- DecisionEngine
- test_replay.py
- SessionDetector
- manifest.json
- test_scaffold.py
- restart.sh

## God Nodes (most connected - your core abstractions)
1. `make_hass()` - 33 edges
2. `patch_track_state_change()` - 31 edges
3. `DecisionEngine` - 29 edges
4. `make_detector()` - 22 edges
5. `t()` - 20 edges
6. `t()` - 19 edges
7. `replay()` - 18 edges
8. `SessionDetector` - 18 edges
9. `DecisionLog` - 17 edges
10. `SessionState` - 16 edges

## Surprising Connections (you probably didn't know these)
- `FakeServices` --uses--> `Decision`  [INFERRED]
  tests/test_init.py → custom_components/shower_guard/decision.py
- `test_decision_log_evicts_oldest_beyond_max_entries()` --calls--> `DecisionLog`  [EXTRACTED]
  tests/test_decision.py → custom_components/shower_guard/decision.py
- `test_decision_log_records_entries_in_order()` --calls--> `DecisionLog`  [EXTRACTED]
  tests/test_decision.py → custom_components/shower_guard/decision.py
- `test_replay_empty_readings_produces_empty_result()` --calls--> `replay()`  [EXTRACTED]
  tests/test_replay.py → custom_components/shower_guard/replay.py
- `FakeServices` --uses--> `SessionState`  [INFERRED]
  tests/test_init.py → custom_components/shower_guard/session.py

## Import Cycles
- None detected.

## Communities (10 total, 1 thin omitted)

### Community 0 - "test_init.py"
Cohesion: 0.11
Nodes (43): callback_for(), make_hass(), patch_track_state_change(), Configuring a presence_sensor disables the duration fallback, even if…, Replace async_track_state_change_event with a spy and return the capture dict.…, Only water_cut_script configured -> no call for WATER_AVAILABLE, but a call is…, A fast humidity rise cuts water immediately through the full wiring, regardless…, Look up the registered callback for a specific entity_id. (+35 more)

### Community 1 - "test_session.py"
Cohesion: 0.09
Nodes (43): make_detector(), datetime, Cooldown expiry emits ENDED and returns to IDLE., Humidity rising during cooldown emits RESUMED and returns to ACTIVE., Low humidity within cooldown window produces no event., StateChange.__str__ should include event name, states, and humidity., Detector respects a custom humidity threshold., Detector respects a custom cooldown duration. (+35 more)

### Community 2 - "DecisionLog"
Cohesion: 0.07
Nodes (29): ConfigEntry, Decision, DecisionLog, DecisionResult, datetime, Enum, Evaluate whether water should remain available. Args: session_state: Current…, Bounded, in-memory audit trail of Decision Engine evaluations (v0.4). Every… (+21 more)

### Community 3 - "DecisionEngine"
Cohesion: 0.09
Nodes (40): DecisionEngine, Decides whether water should remain available. Policies (checked in order): 1.…, datetime, Without humidity/active_since_humidity, there is no trigger at all (besides…, COOLDOWN counts toward the same session — delta still applies., Without max_session_seconds, a long session with a low delta stays available…, With max_session_seconds set, a low-delta session is still capped once it runs…, With max_session_seconds set, water stays available before the limit. (+32 more)

### Community 4 - "test_replay.py"
Cohesion: 0.19
Nodes (21): load_readings_from_csv(), _main(), Replay a sequence of ``(timestamp, humidity)`` readings, oldest first, through…, Load ``(timestamp, humidity)`` readings from a CSV file with ``timestamp`` and…, CLI: replay a CSV file of readings and print the results., replay(), Reading, datetime (+13 more)

### Community 5 - "SessionDetector"
Cohesion: 0.17
Nodes (11): Output of a replay run — the same artifacts production would produce., ReplayResult, datetime, Humidity reading used as the current baseline: set on STARTED, reset to the…, Process a new humidity reading. Args: humidity: Current relative humidity (%).…, Describes a single session state transition., Stateful humidity-based shower session detector. Feed humidity readings via…, Current session state. (+3 more)

### Community 6 - "manifest.json"
Cohesion: 0.20
Nodes (9): codeowners, dependencies, documentation, domain, iot_class, issue_tracker, name, requirements (+1 more)

### Community 7 - "test_scaffold.py"
Cohesion: 0.22
Nodes (8): DOMAIN must equal 'shower_guard'., VERSION must be set and follow semver major.minor.patch format., manifest.json must exist alongside the package., manifest.json domain must match DOMAIN constant., test_const_domain(), test_const_version(), test_manifest_domain(), test_manifest_exists()

## Knowledge Gaps
- **10 isolated node(s):** `domain`, `name`, `version`, `documentation`, `issue_tracker` (+5 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SessionState` connect `DecisionLog` to `test_init.py`, `test_session.py`, `DecisionEngine`, `test_replay.py`, `SessionDetector`?**
  _High betweenness centrality (0.218) - this node is a cross-community bridge._
- **Why does `DecisionEngine` connect `DecisionEngine` to `DecisionLog`, `test_replay.py`, `SessionDetector`?**
  _High betweenness centrality (0.134) - this node is a cross-community bridge._
- **Why does `SessionDetector` connect `SessionDetector` to `test_session.py`, `DecisionLog`, `test_replay.py`?**
  _High betweenness centrality (0.107) - this node is a cross-community bridge._
- **Are the 2 inferred relationships involving `DecisionEngine` (e.g. with `SessionState` and `ReplayResult`) actually correct?**
  _`DecisionEngine` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `domain`, `name`, `version` to the rest of the system?**
  _10 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_init.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11416490486257928 - nodes in this community are weakly interconnected._
- **Should `test_session.py` be split into smaller, more focused modules?**
  _Cohesion score 0.08562367864693446 - nodes in this community are weakly interconnected._