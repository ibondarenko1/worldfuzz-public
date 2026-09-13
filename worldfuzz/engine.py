"""Bounded synthetic search, replay, and artifact verification."""
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import random
import tempfile

from . import __version__
from .schema import ScenarioSpec
from .simulator import GOAL_X_M, HORIZON_S, simulate, evaluate_trace, trace_digest


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def write_json(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + "-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def has_violation(run, monitor="human_distance"):
    return any(item["monitor"] == monitor for item in run["summary"]["violations"])


def nominal_control_scenario(scenario):
    """Remove hazards while keeping the requested mission and its parameters."""
    return replace(scenario, human_present=False, sensor_delay_ms=0,
                   occlusion=False, sensor_conflict=False,
                   command_source="operator", command_priority="normal")


def mission_feasibility(scenario):
    """Check the empty-route time budget, independently of any policy outcome.

    A feasible nominal mission can still time out after protective stops. This
    check is experiment metadata and does not provide world state to a policy.
    """
    nominal_travel_time_s = GOAL_X_M / scenario.robot_speed_mps
    return {"nominal_travel_time_s": round(nominal_travel_time_s, 6),
            "horizon_s": HORIZON_S,
            "nominal_completion_possible": nominal_travel_time_s <= HORIZON_S}


def _comparison_result(replay_confirmed, blocked, preserved, mission_completed,
                       feasibility=None):
    if feasibility is not None and not feasibility["nominal_completion_possible"]:
        return "MISSION_INFEASIBLE_AT_REQUESTED_SPEED"
    if replay_confirmed and blocked and preserved:
        return ("FIX_CONFIRMED_FOR_THIS_SCENARIO" if mission_completed
                else "SAFETY_BLOCKED_MISSION_INCOMPLETE")
    return "COMPARISON_INCOMPLETE"


def random_scenarios(seed, count):
    rng = random.Random(seed)
    return [ScenarioSpec(
        seed=rng.randrange(2**32),
        human_crossing_time_s=round(rng.uniform(1.0, 12.0), 3),
        human_speed_mps=round(rng.uniform(0.3, 1.5), 3),
        sensor_delay_ms=rng.choice([0, 150, 300, 600, 900, 1200, 1500]),
        occlusion=rng.random() < 0.4,
        robot_speed_mps=round(rng.uniform(0.3, 1.2), 3),
        sensor_conflict=rng.random() < 0.15,
        command_source=rng.choice(["operator", "guest"]),
        command_priority="override" if rng.random() < 0.25 else "normal",
    ) for _ in range(count)]


def search(seed=42, budget=24, planner=None, target="human_distance", *,
           checkpoint=None, max_model_calls=None):
    from .campaign import search as campaign_search
    return campaign_search(seed, budget, planner, target, checkpoint=checkpoint,
                           max_model_calls=max_model_calls)


def minimize(scenario, target="human_distance"):
    current = scenario
    initial = simulate(current, "baseline")
    if not has_violation(initial, target):
        raise ValueError("Minimization requires a confirmed target violation")
    resets = {"sensor_delay_ms": 0, "occlusion": False, "sensor_conflict": False,
              "command_priority": "normal", "command_source": "operator"}
    attempts = []
    changed = True
    # Fixed-point deletion: removal can make a previously necessary factor redundant.
    while changed:
        changed = False
        for name, value in resets.items():
            if getattr(current, name) == value:
                continue
            candidate = replace(current, **{name: value})
            run = simulate(candidate, "baseline")
            retained_failure = has_violation(run, target)
            attempts.append({"removed": name, "retained_failure": retained_failure,
                             "scenario": candidate.as_dict(), "summary": run["summary"],
                             "trace_sha256": run["trace_sha256"]})
            if retained_failure:
                current = candidate
                changed = True
    remaining = [name for name, value in resets.items() if getattr(current, name) != value]
    return current, {"method": "fixed_point_single_factor_deletion",
                     "scope": "1-minimal under listed resets; timing, speed and seed stay fixed",
                     "target": target, "resets": resets, "remaining_factors": remaining,
                     "initial_factors": [n for n,v in resets.items() if getattr(scenario,n) != v],
                     "executed_runs": 1 + len(attempts), "attempts": attempts}


def source_fingerprint():
    root = Path(__file__).parent
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob("*.py"))}


def create_capsule(directory, result, *, allow_campaign_journal=False):
    directory = Path(directory)
    is_campaign = result.get("campaign_schema_version") == 1
    if directory.exists() and any(directory.iterdir()):
        journal = directory / "campaign.json"
        if (not allow_campaign_journal or not is_campaign or journal.is_symlink()
                or set(p.name for p in directory.iterdir()) != {"campaign.json"}
                or json.loads(journal.read_text()) != result):
            raise ValueError("Use an empty output directory; existing evidence is not overwritten")
    directory.mkdir(parents=True, exist_ok=True)
    if is_campaign:
        # The journal captures the source used for search; reduction must not
        # silently run a different source and claim one coherent experiment.
        if result.get("source_sha256") != source_fingerprint():
            raise ValueError("Source changed during campaign; preserve the journal and start a new run")
        if not (directory / "campaign.json").exists():
            write_json(directory / "campaign.json", result)
    if not result["found"]:
        if is_campaign:
            payloads = {"campaign.json": result}
            manifest = {"schema_version": 3, "evidence_kind": "campaign",
                        "integrity": "SHA-256 content integrity, not signed authenticity",
                        "files": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                                  for name in payloads}}
            write_json(directory / "manifest.json", manifest)
            return {"status": "INCOMPLETE" if result["status"] == "RUNNING" else result["status"],
                    "campaign_result": result["status"], "directory": str(directory),
                    "search_runs": result["executed_runs"], "usage": result["usage"],
                    "execution_counts": result["execution_counts"]}
        write_json(directory / "search.json", result)
        return {"status": "NO_COUNTEREXAMPLE_FOUND", "directory": str(directory),
                "search_runs": result["executed_runs"]}
    scenario, reduction = minimize(ScenarioSpec.from_dict(result["found"]["scenario"]), result["target"])
    replays = [simulate(scenario, "baseline") for _ in range(3)]
    baseline = replays[0]
    hardened = simulate(scenario, "hardened")
    control_scenario = nominal_control_scenario(scenario)
    controls = [simulate(control_scenario, p) for p in ("baseline", "hardened")]
    feasibility = mission_feasibility(scenario)
    same_trace = len({r["trace_sha256"] for r in replays}) == 1
    replay_confirmed = same_trace and all(has_violation(r, result["target"]) for r in replays)
    blocked = not hardened["summary"]["violations"]
    mission_preserved = all(r["summary"]["mission_completed"] and not r["summary"]["violations"] for r in controls)
    comparison = {
        "target": result["target"], "baseline_confirmed": has_violation(baseline, result["target"]),
        "three_replays_identical": same_trace, "replay_confirmed": replay_confirmed,
        "hardened_blocks_all_monitored_violations": blocked,
        "hardened_scenario_mission_completed": hardened["summary"]["mission_completed"],
        "positive_controls_mission_preserved": mission_preserved,
        "mission_feasibility": feasibility,
        "result": _comparison_result(replay_confirmed, blocked, mission_preserved,
                                     hardened["summary"]["mission_completed"], feasibility),
        "scope": "Synthetic kinematic fixture only. No global safety or certification claim.",
        "runs": {"search": result["executed_runs"], "minimization": reduction["executed_runs"],
                 "baseline_replays": 3, "hardened_replay": 1, "positive_controls": 2},
    }
    comparison["runs"]["total"] = sum(comparison["runs"].values())
    search_record = {k: v for k, v in result.items() if k != "found"}
    payloads = {
        "scenario.json": scenario.as_dict(), "baseline.json": baseline,
        "hardened.json": hardened, "control.json": controls[1], "control_baseline.json": controls[0],
        "search.json": search_record, "minimization.json": reduction,
        "replays.json": {"count": 3, "trace_sha256": [r["trace_sha256"] for r in replays]},
        "comparison.json": comparison,
        "provenance.json": {"worldfuzz_version": __version__, "executor": "local_python",
                            "simulation": "synthetic_2d_kinematic", "strategy": result["strategy"],
                            "model_calls": result["model_calls"], "source_sha256": source_fingerprint(),
                            "nebius_execution": False},
    }
    if is_campaign:
        payloads["campaign.json"] = result
    for name, payload in payloads.items():
        if name == "campaign.json":
            # The matching durable journal was already saved before reduction.
            continue
        write_json(directory / name, payload)
    manifest = {"schema_version": 3 if is_campaign else 2, "integrity": "SHA-256 content integrity, not signed authenticity",
                "files": {name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in sorted(payloads)}}
    if is_campaign:
        manifest["evidence_kind"] = "finding"
    write_json(directory / "manifest.json", manifest)
    outcome = {"status": comparison["result"], "directory": str(directory), **comparison}
    if is_campaign:
        outcome.update(campaign_result=result["status"], usage=result["usage"],
                       execution_counts=result["execution_counts"])
    return outcome


def verify_capsule(directory):
    directory = Path(directory)
    from .campaign import verify_campaign
    if not (directory / "manifest.json").exists():
        journal = directory / "campaign.json"
        if journal.is_symlink():
            raise ValueError("Campaign journal must be a regular file")
        checked = verify_campaign(json.loads(journal.read_text()))
        # Search may finish before capsule materialization is interrupted. A
        # standalone journal always describes incomplete artifact construction.
        return {**checked, "result": "INCOMPLETE", "files_checked": 1,
                "artifact_complete": False}
    manifest = json.loads((directory / "manifest.json").read_text())
    schema_version = manifest.get("schema_version")
    if type(schema_version) is not int or schema_version not in (1, 2, 3):
        raise ValueError("Unsupported capsule schema version")
    required = {"scenario.json", "baseline.json", "hardened.json", "control.json", "control_baseline.json",
                "search.json", "minimization.json", "replays.json", "comparison.json", "provenance.json"}
    if schema_version == 3:
        if manifest.get("evidence_kind") == "campaign":
            required = {"campaign.json"}
        elif manifest.get("evidence_kind") == "finding":
            required.add("campaign.json")
        else:
            raise ValueError("Unsupported campaign evidence kind")
    if set(manifest.get("files", {})) != required:
        raise ValueError("Manifest must list the complete expected evidence set")
    for name, digest in manifest["files"].items():
        path = directory / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Artifact integrity mismatch: {name}")
    data = {name: json.loads((directory / name).read_text()) for name in required}
    campaign_verified = None
    if schema_version == 3:
        campaign_verified = verify_campaign(data["campaign.json"])
        if manifest["evidence_kind"] == "campaign":
            if data["campaign.json"].get("found") is not None:
                raise ValueError("Finding campaign lacks comparison evidence")
            return {**campaign_verified, "files_checked": 1, "artifact_complete": True}
        if campaign_verified["campaign_result"] != "FOUND":
            raise ValueError("Finding capsule lacks a completed finding campaign")
        expected_search = {k: v for k, v in data["campaign.json"].items() if k != "found"}
        if data["search.json"] != expected_search:
            raise ValueError("Search record differs from campaign journal")
        if data["comparison.json"].get("target") != data["campaign.json"]["target"]:
            raise ValueError("Comparison target differs from campaign target")
        if (data["provenance.json"].get("model_calls") != data["campaign.json"]["model_calls"]
                or data["provenance.json"].get("source_sha256") != data["campaign.json"]["source_sha256"]
                or data["provenance.json"].get("strategy") != data["campaign.json"]["strategy"]):
            raise ValueError("Capsule provenance differs from campaign journal")
    scenario = ScenarioSpec.from_dict(data["scenario.json"])
    # Version 1 controls used the default speed and timing. Verify those exact
    # historical experiments without silently upgrading their evidence.
    control_scenario = (nominal_control_scenario(scenario) if schema_version >= 2
                        else ScenarioSpec(seed=scenario.seed, human_present=False))
    for name, policy in (("baseline.json", "baseline"), ("hardened.json", "hardened"),
                         ("control.json", "hardened"), ("control_baseline.json", "baseline")):
        recorded = data[name]
        expected_scenario = scenario if not name.startswith("control") else control_scenario
        if recorded["scenario"] != expected_scenario.as_dict() or recorded["policy"] != policy:
            raise ValueError(f"Scenario or policy mismatch: {name}")
        if trace_digest(recorded["trace"]) != recorded["trace_sha256"]:
            raise ValueError(f"Trace digest mismatch: {name}")
        if evaluate_trace(recorded["trace"], expected_scenario.safety_distance_m) != recorded["summary"]:
            raise ValueError(f"Monitor recomputation mismatch: {name}")
        rerun = simulate(expected_scenario, policy)
        if rerun["trace_sha256"] != recorded["trace_sha256"] or rerun["summary"] != recorded["summary"]:
            raise ValueError(f"Replay mismatch: {name}")
    comparison = data["comparison.json"]
    replay_hashes = data["replays.json"]["trace_sha256"]
    expected_replay = data["replays.json"].get("count") == 3 and replay_hashes == [data["baseline.json"]["trace_sha256"]] * 3
    target_confirmed = has_violation(data["baseline.json"], comparison["target"])
    blocked = not data["hardened.json"]["summary"]["violations"]
    preserved = all(data[n]["summary"]["mission_completed"] and not data[n]["summary"]["violations"]
                    for n in ("control.json", "control_baseline.json"))
    feasibility = mission_feasibility(scenario) if schema_version >= 2 else None
    expected = {"baseline_confirmed": target_confirmed, "three_replays_identical": expected_replay,
                "replay_confirmed": expected_replay and target_confirmed,
                "hardened_blocks_all_monitored_violations": blocked,
                "hardened_scenario_mission_completed": data["hardened.json"]["summary"]["mission_completed"],
                "positive_controls_mission_preserved": preserved,
                "result": _comparison_result(expected_replay and target_confirmed, blocked, preserved,
                                             data["hardened.json"]["summary"]["mission_completed"], feasibility)}
    if schema_version >= 2:
        expected["mission_feasibility"] = feasibility
    for key, value in expected.items():
        if comparison.get(key) != value:
            raise ValueError(f"Comparison mismatch: {key}")
    history = data["search.json"]["history"]
    if not history or len(history) != data["search.json"]["executed_runs"]:
        raise ValueError("Search history does not match the recorded run count")
    for index, record in enumerate(history if campaign_verified is None else [], 1):
        recorded_scenario = ScenarioSpec.from_dict(record["scenario"])
        rerun = simulate(recorded_scenario, "baseline")
        if record["index"] != index or rerun["summary"] != record["summary"]:
            raise ValueError("Search history replay mismatch")
        if "nominal_mission_feasible" in record and record["nominal_mission_feasible"] is not mission_feasibility(recorded_scenario)["nominal_completion_possible"]:
            raise ValueError("Search history mission feasibility mismatch")
    original = ScenarioSpec.from_dict(history[-1]["scenario"])
    reduced, reduction_record = minimize(original, comparison["target"])
    if reduced != scenario or reduction_record != data["minimization.json"]:
        raise ValueError("Minimization replay mismatch")
    expected_counts = {"search": len(history), "minimization": reduction_record["executed_runs"],
                       "baseline_replays": 3, "hardened_replay": 1, "positive_controls": 2}
    expected_counts["total"] = sum(expected_counts.values())
    if comparison.get("runs") != expected_counts:
        raise ValueError("Run accounting mismatch")
    return {"status": "VERIFIED", "files_checked": len(required), "replayed_policies": 4,
            "result": expected["result"],
            "replayed_search_runs": len(history),
            "verification_runs": 4 + len(history) + reduction_record["executed_runs"],
            "verification_scope": "artifact hashes, simulation traces, monitors, search outcomes, minimization, comparison, run counts",
            "model_provenance_authenticated": False,
            "source_matches": data["provenance.json"]["source_sha256"] == source_fingerprint()}
