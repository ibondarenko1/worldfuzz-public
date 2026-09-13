"""Fixed, local-only tuning pilot. This is not a held-out AI benchmark.

No planner, provider adapter, runtime credential or network client is imported.
Every local simulation has an atomic before/after journal. Unfinished attempts
remain uncertain and an existing run is never resumed or silently retried.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time

from .cases import CaseSpec, TUNING_CASES
from .engine import json_bytes, source_fingerprint, write_json
from .schema import ScenarioSpec
from .simulator import evaluate_trace, simulate, trace_digest


METHODS = ("random", "crossing_time")
TRIALS_PER_METHOD = 48
PREFIX_BUDGET = 12
TIMING_OFFSETS_S = (0.0, -0.15, 0.15, -0.35, 0.35, -0.75,
                    0.75, -1.25, 1.25, -2.0, 2.0, 3.0)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sha(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def _same(left, right):
    return json_bytes(left) == json_bytes(right)


def _cases():
    if len(TUNING_CASES) != 2 or any(case.split != "tuning" for case in TUNING_CASES):
        raise ValueError("This pilot accepts exactly the two declared tuning cases")
    return TUNING_CASES


def _check_case(case):
    if not isinstance(case, CaseSpec) or not any(
            _same(case.as_dict(), known.as_dict()) for known in _cases()):
        raise ValueError("Only the unchanged declared tuning cases may enter this pilot")


def _speed_pairs(case):
    robot_low, robot_high = case.robot_speed_bounds
    human_low, human_high = case.human_speed_bounds
    return ((robot_low, human_high), (robot_high, human_low),
            ((robot_low + robot_high) / 2, (human_low + human_high) / 2),
            (robot_high, human_high))


def candidates(case, method):
    """Return all 48 proposals without observing any simulator outcome."""
    _check_case(case)
    if method not in METHODS:
        raise ValueError("Unknown offline pilot method")
    scenarios = []
    if method == "random":
        rng = random.Random(case.world_seed)
        for _ in range(TRIALS_PER_METHOD):
            scenarios.append(case.scenario(
                human_crossing_time_s=round(rng.uniform(*case.crossing_time_bounds), 3),
                human_speed_mps=round(rng.uniform(*case.human_speed_bounds), 3),
                robot_speed_mps=round(rng.uniform(*case.robot_speed_bounds), 3),
                sensor_delay_ms=rng.choice(case.sensor_delays_ms),
                occlusion=rng.choice(case.occlusion_choices),
            ))
    else:
        pairs = _speed_pairs(case)
        low, high = case.crossing_time_bounds
        for index in range(TRIALS_PER_METHOD):
            robot_speed, human_speed = pairs[index % len(pairs)]
            delay = case.sensor_delays_ms[index % len(case.sensor_delays_ms)]
            occlusion = case.occlusion_choices[
                (index // len(case.sensor_delays_ms)) % len(case.occlusion_choices)]
            # Public contract: unavailable observations cause this policy to
            # stop. Before its first observation, warming-up takes delay/1000;
            # a crossing beyond the robot's t=2 position adds the t=2..6 blackout.
            estimated_arrival = case.crossing_x_m / robot_speed + delay / 1000
            if occlusion and estimated_arrival >= 2.0:
                estimated_arrival += 4.0
            crossing_time = min(high, max(low, estimated_arrival +
                                         TIMING_OFFSETS_S[index // len(pairs)]))
            scenarios.append(case.scenario(
                human_crossing_time_s=round(crossing_time, 3),
                human_speed_mps=human_speed, robot_speed_mps=robot_speed,
                sensor_delay_ms=delay, occlusion=occlusion,
            ))
    for scenario in scenarios:
        case.validate_scenario(scenario)
    return scenarios


def pilot_plan():
    """Serializable pre-run contract; excludes clocks and observed outcomes."""
    return {
        "pilot_schema_version": 1,
        "experiment": "offline-tuning-difficulty-v1",
        "split": "tuning",
        "cases": [case.as_dict() for case in _cases()],
        "methods": list(METHODS),
        "trials_per_method_case": TRIALS_PER_METHOD,
        "prefix_budget": PREFIX_BUDGET,
        "complete_budget_after_finding": True,
        "target_monitor": "human_distance",
        "method_configuration": {
            "random": {"algorithm": "python-random-uniform-rounded-3-decimals-v1",
                       "seed": "case.world_seed", "sampling": "with_replacement"},
            "crossing_time": {
                "algorithm": "public-crossing-time-grid-v1",
                "timing_offsets_s": list(TIMING_OFFSETS_S),
                "speed_pairs_by_case": {case.case_id: [list(pair) for pair in _speed_pairs(case)]
                                        for case in _cases()},
                "order": "offset outer; four speed pairs inner",
                "delay_choice": "index modulo delay count",
                "occlusion_choice": "floor(index/delay_count) modulo occlusion count",
                "arrival_estimate": "crossing_x/robot_speed + sensor_delay_ms/1000",
                "blackout_adjustment": "add 4 seconds if occluded and estimated arrival >= 2 seconds",
                "domain_clipping": "clip crossing time to case bounds then round to 3 decimals",
            },
        },
        "limits": {"search_simulations": 192, "hardened_comparison_simulations": 192,
                   "empty_route_controls": 4, "total_campaign_simulations": 388,
                   "provider_requests": 0, "cloud_jobs": 0, "automatic_retries": 0},
        "representative_selection": "lowest search minimum distance; earliest index breaks ties; original traces retained",
        "empty_route_control_scope": "Task-level smoke controls at each case midpoint speed; not a matched-speed control for every proposal.",
        "difficulty_rule": {
            "too_easy_for_feedback": "both methods first finding <= 2",
            "no_observed_failure": "neither method finds a violation in all 48 trials",
            "mixed_difficulty": "all other complete outcomes; does not establish AI benefit",
        },
        "interpretation": [
            "Two tuning tasks only; no held-out task is instantiated or evaluated.",
            "Explicit crossing geometry makes these worlds deterministic; world_seed identifies the case and seeds random proposals, not independent stochastic world draws.",
            "Both methods have the same allowed parameter domain and complete all 48 search trials.",
            "Every search proposal is also evaluated with the existing hardened policy; these 192 comparisons are separate from search costs.",
            "No model is used, so this pilot cannot establish model advantage or feedback effectiveness.",
            "Failures or interrupted simulations have uncertain execution; they are not counted as zero actual work.",
        ],
        "source_sha256": source_fingerprint(),
    }


def _schedule():
    result = []
    for case in _cases():
        for method in METHODS:
            for index, scenario in enumerate(candidates(case, method), 1):
                for kind, policy in (("search", case.policy), ("hardened", "hardened")):
                    result.append({"id": len(result) + 1, "kind": kind,
                                   "case_id": case.case_id, "method": method,
                                   "index": index, "policy": policy,
                                   "scenario": scenario.as_dict()})
    for case in _cases():
        control = replace(case.scenario(), human_present=False, sensor_delay_ms=0,
                          occlusion=False, sensor_conflict=False,
                          command_source="operator", command_priority="normal")
        for policy in (case.policy, "hardened"):
            result.append({"id": len(result) + 1, "kind": "control", "case_id": case.case_id,
                           "method": None, "index": None, "policy": policy,
                           "scenario": control.as_dict()})
    return result


def _finding(summary):
    return any(item["monitor"] == "human_distance" for item in summary["violations"])


def _successful(operations):
    return [operation for operation in operations if operation["status"] == "succeeded"]


def _trials(operations):
    succeeded = _successful(operations)
    hardened = {(op["case_id"], op["method"], op["index"]): op
                for op in succeeded if op["kind"] == "hardened"}
    result = []
    for op in succeeded:
        if op["kind"] != "search":
            continue
        comparison = hardened.get((op["case_id"], op["method"], op["index"]))
        result.append({key: op[key] for key in
                       ("case_id", "method", "index", "scenario", "summary", "trace_sha256")})
        result[-1]["hardened"] = ({key: comparison[key] for key in ("summary", "trace_sha256")}
                                   if comparison else None)
    return result


def _metrics(trials, budget):
    selected = [trial for trial in trials if trial["index"] <= budget]
    found = [trial["index"] for trial in selected if _finding(trial["summary"])]
    comparisons = [trial["hardened"] for trial in selected if trial["hardened"] is not None]
    seen = set()
    duplicates = 0
    for trial in selected:
        digest = _sha(trial["scenario"])
        duplicates += digest in seen
        seen.add(digest)
    return {
        "budget": budget, "executed_search_trials": len(selected),
        "complete": len(selected) == budget,
        "finding_status": ("FOUND" if found else "NO_COUNTEREXAMPLE_FOUND"
                           if len(selected) == budget else "INCOMPLETE"),
        "first_finding": found[0] if found else None,
        "findings": len(found),
        "best_margin_m": (round(min(trial["summary"]["minimum_distance_m"] - 0.75
                                    for trial in selected), 6) if selected else None),
        "mission_completed": sum(trial["summary"]["mission_completed"] for trial in selected),
        "duplicates": duplicates,
        "hardened_completed": len(comparisons),
        "hardened_findings": sum(_finding(item["summary"]) for item in comparisons),
        "hardened_mission_completed": sum(item["summary"]["mission_completed"] for item in comparisons),
        "hardened_safe_and_mission_completed": sum(
            not item["summary"]["violations"] and item["summary"]["mission_completed"]
            for item in comparisons),
    }


def _summary(journal):
    operations = journal["operations"]
    succeeded = _successful(operations)
    trials = _trials(operations)
    aggregates = []
    difficulty = []
    for case in _cases():
        case_metrics = []
        for method in METHODS:
            group = [trial for trial in trials if trial["case_id"] == case.case_id and trial["method"] == method]
            budgets = {str(budget): _metrics(group, budget) for budget in (PREFIX_BUDGET, TRIALS_PER_METHOD)}
            aggregates.append({"case_id": case.case_id, "method": method, "budgets": budgets})
            case_metrics.append(budgets[str(TRIALS_PER_METHOD)])
        if not all(item["complete"] for item in case_metrics):
            flag = "incomplete"
        elif all(item["first_finding"] is not None and item["first_finding"] <= 2 for item in case_metrics):
            flag = "too_easy_for_feedback"
        elif all(item["first_finding"] is None for item in case_metrics):
            flag = "no_observed_failure"
        else:
            flag = "mixed_difficulty"
        difficulty.append({"case_id": case.case_id, "flag": flag})
    return {
        "pilot_schema_version": 1, "status": journal["status"],
        "plan_sha256": journal["plan_sha256"],
        "counts": {"attempted_simulations": len(operations),
                   "completed_simulations": len(succeeded),
                   "uncertain_simulation_attempts": len(operations) - len(succeeded),
                   "completed_search": sum(op["kind"] == "search" for op in succeeded),
                   "completed_hardened": sum(op["kind"] == "hardened" for op in succeeded),
                   "completed_controls": sum(op["kind"] == "control" for op in succeeded),
                   "provider_requests": 0, "cloud_jobs": 0},
        "aggregates": aggregates, "difficulty": difficulty, "trials": trials,
        "controls": [{key: op[key] for key in ("case_id", "policy", "scenario", "summary", "trace_sha256")}
                     for op in succeeded if op["kind"] == "control"],
        "elapsed_seconds": journal["elapsed_seconds"],
        "interpretation": "Tuning-only software-policy evidence; no AI comparison or real-robot safety claim.",
    }


def _representative_name(case_id, method):
    return f"representative-{case_id}-{method}.json"


def _best_pairs(operations):
    best = {}
    for trial in _trials(operations):
        if trial["hardened"] is None:
            continue
        key = (trial["case_id"], trial["method"])
        if key not in best or trial["summary"]["minimum_distance_m"] < best[key]["summary"]["minimum_distance_m"]:
            best[key] = trial
    return best


def _finish(directory, journal):
    write_json(directory / "pilot.json", journal)
    summary = _summary(journal)
    write_json(directory / "result.json", summary)
    if journal["status"] != "COMPLETED":
        return summary
    payloads = ["plan.json", "pilot.json", "result.json"]
    payloads += [_representative_name(*key) for key in _best_pairs(journal["operations"])]
    write_json(directory / "manifest.json", {
        "pilot_schema_version": 1,
        "files": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                  for name in payloads},
    })
    return summary


def run_pilot(out):
    """Run the predeclared free pilot once, preserving partial evidence on errors."""
    directory = Path(out)
    plan = pilot_plan()
    if directory.is_symlink():
        raise ValueError("Pilot output must be an ordinary directory")
    if directory.exists():
        entries = list(directory.iterdir())
        if entries and (len(entries) != 1 or entries[0].name != "plan.json"
                        or entries[0].is_symlink() or not _same(_read(entries[0]), plan)):
            raise ValueError("Use an empty directory or an unchanged predeclared plan only")
    directory.mkdir(parents=True, exist_ok=True)
    journal = {"pilot_schema_version": 1, "status": "RUNNING", "plan_sha256": _sha(plan),
               "started_at": _now(), "finished_at": None, "elapsed_seconds": 0.0,
               "operations": [], "error": None}
    # Exclusive creation reserves the run before any simulation. Two callers
    # cannot overwrite one another after racing through the empty-folder check.
    with (directory / "pilot.json").open("xb") as handle:
        handle.write(json_bytes(journal))
        handle.flush()
        os.fsync(handle.fileno())
    if not (directory / "plan.json").exists():
        write_json(directory / "plan.json", plan)
    start = time.monotonic()
    by_id = {case.case_id: case for case in _cases()}
    current_search = None
    best_distances = {}
    try:
        for expected in _schedule():
            operation = dict(expected, status="pending", started_at=_now(), finished_at=None)
            journal["operations"].append(operation)
            write_json(directory / "pilot.json", journal)
            case = by_id[operation["case_id"]]
            run = simulate(ScenarioSpec.from_dict(operation["scenario"]), operation["policy"],
                           crossing_x_m=case.crossing_x_m)
            operation.update(status="succeeded", finished_at=_now(), summary=run["summary"],
                             trace_sha256=run["trace_sha256"])
            write_json(directory / "pilot.json", journal)
            if operation["kind"] == "search":
                current_search = run
            elif operation["kind"] == "hardened":
                key = (case.case_id, operation["method"])
                distance = current_search["summary"]["minimum_distance_m"]
                if key not in best_distances or distance < best_distances[key]:
                    write_json(directory / _representative_name(*key), {
                        "case_id": key[0], "method": key[1], "index": operation["index"],
                        "search": current_search, "hardened": run,
                    })
                    best_distances[key] = distance
                current_search = None
        journal["status"] = "COMPLETED"
    except (Exception, KeyboardInterrupt) as exc:
        cancelled = isinstance(exc, KeyboardInterrupt)
        journal["status"] = "CANCELLED" if cancelled else "FAILED"
        if journal["operations"] and journal["operations"][-1]["status"] == "pending":
            journal["operations"][-1].update(status="cancelled" if cancelled else "failed", finished_at=_now())
        journal["error"] = {"code": "interrupted" if cancelled else "local_execution_failed",
                            "message": "The pilot stopped; completed evidence is retained and no retry was made."}
    journal["finished_at"] = _now()
    journal["elapsed_seconds"] = round(time.monotonic() - start, 6)
    return _finish(directory, journal)


def _read(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError("Evidence must be an ordinary file")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    def bad_constant(_):
        raise ValueError("Non-finite JSON constant")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs, parse_constant=bad_constant)


def _timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Missing timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("Timestamp must include a timezone")
    return parsed


def verify_pilot(out):
    """Replay each completed simulation once; do not retry unfinished attempts."""
    directory = Path(out)
    if directory.is_symlink():
        raise ValueError("Pilot directory must not be a symlink")
    plan = _read(directory / "plan.json")
    expected_plan = pilot_plan()
    source_matches = _same(plan.get("source_sha256"), expected_plan["source_sha256"])
    expected_plan["source_sha256"] = plan.get("source_sha256")
    if not _same(plan, expected_plan):
        raise ValueError("Pilot plan differs from the declared tuning contract")
    recorded_sources = plan["source_sha256"]
    if (not isinstance(recorded_sources, dict) or not recorded_sources
            or any(not isinstance(name, str) or not name.endswith(".py") or "/" in name or "\\" in name
                   or not isinstance(digest, str) or len(digest) != 64
                   or any(c not in "0123456789abcdef" for c in digest)
                   for name, digest in recorded_sources.items())):
        raise ValueError("Invalid recorded source fingerprint")
    journal = _read(directory / "pilot.json")
    expected_keys = {"pilot_schema_version", "status", "plan_sha256", "started_at", "finished_at",
                     "elapsed_seconds", "operations", "error"}
    if set(journal) != expected_keys or type(journal["pilot_schema_version"]) is not int or journal["pilot_schema_version"] != 1:
        raise ValueError("Invalid pilot journal schema")
    if journal["plan_sha256"] != _sha(plan) or journal["status"] not in {"RUNNING", "COMPLETED", "FAILED", "CANCELLED"}:
        raise ValueError("Invalid journal plan or status")
    begin = _timestamp(journal["started_at"])
    if journal["status"] == "RUNNING":
        if journal["finished_at"] is not None or journal["error"] is not None:
            raise ValueError("Running journal has terminal fields")
    else:
        if _timestamp(journal["finished_at"]) < begin:
            raise ValueError("Terminal time precedes start")
        expected_error = (None if journal["status"] == "COMPLETED" else {
            "code": "interrupted" if journal["status"] == "CANCELLED" else "local_execution_failed",
            "message": "The pilot stopped; completed evidence is retained and no retry was made."})
        if not _same(journal["error"], expected_error):
            raise ValueError("Terminal error/status mismatch")
    if type(journal["elapsed_seconds"]) not in (int, float) or not math.isfinite(journal["elapsed_seconds"]) or journal["elapsed_seconds"] < 0:
        raise ValueError("Invalid elapsed time")
    operations = journal["operations"]
    schedule = _schedule()
    if not isinstance(operations, list) or len(operations) > len(schedule):
        raise ValueError("Pilot exceeds its operation budget")
    if journal["status"] == "COMPLETED" and len(operations) != len(schedule):
        raise ValueError("Completed pilot did not consume its complete budget")
    by_id = {case.case_id: case for case in _cases()}
    replayed = {}
    replay_count = 0
    for position, operation in enumerate(operations):
        expected = schedule[position]
        if not isinstance(operation, dict) or not _same({key: operation.get(key) for key in expected}, expected):
            raise ValueError("Operation order, proposal or policy differs from the deterministic schedule")
        if operation.get("status") not in {"pending", "succeeded", "failed", "cancelled"}:
            raise ValueError("Invalid operation status")
        op_keys = set(expected) | {"status", "started_at", "finished_at"}
        if operation["status"] == "succeeded":
            op_keys |= {"summary", "trace_sha256"}
        if set(operation) != op_keys:
            raise ValueError("Invalid operation evidence fields")
        op_begin = _timestamp(operation["started_at"])
        if op_begin < begin:
            raise ValueError("Operation began before pilot")
        if operation["status"] == "pending":
            if operation["finished_at"] is not None or journal["status"] != "RUNNING":
                raise ValueError("Pending simulation has inconsistent terminal metadata")
        elif _timestamp(operation["finished_at"]) < op_begin:
            raise ValueError("Operation ended before it began")
        if operation["status"] != "succeeded":
            if position != len(operations) - 1 or journal["status"] == "COMPLETED":
                raise ValueError("An unfinished simulation cannot be followed by more work")
            if operation["status"] in {"failed", "cancelled"} and journal["status"] != operation["status"].upper():
                raise ValueError("Failed simulation/status mismatch")
            continue
        case = by_id[operation["case_id"]]
        run = simulate(ScenarioSpec.from_dict(operation["scenario"]), operation["policy"], crossing_x_m=case.crossing_x_m)
        replay_count += 1
        if not _same(run["summary"], operation["summary"]) or run["trace_sha256"] != operation["trace_sha256"]:
            raise ValueError("Recorded simulation differs from independent replay")
        # Retain replay metadata and its trace digest, not 388 full traces.
        replayed[operation["id"]] = {key: value for key, value in run.items() if key != "trace"}
    expected_summary = _summary(journal)
    result_path = directory / "result.json"
    if result_path.exists() and not _same(_read(result_path), expected_summary):
        raise ValueError("Recorded aggregate or trial result differs from journal")
    expected_representatives = _best_pairs(operations)
    final_manifest = (directory / "manifest.json").exists()
    if final_manifest and journal["status"] != "COMPLETED":
        raise ValueError("An incomplete pilot must not have a completed manifest")
    representatives_checked = 0
    expected_files = {"plan.json", "pilot.json", "result.json"}
    for key, trial in expected_representatives.items():
        name = _representative_name(*key)
        expected_files.add(name)
        path = directory / name
        if not final_manifest:
            # A hard stop between the primary journal and its optional trace
            # copy can leave that copy missing or stale. Verify the primary
            # evidence, but make no claim about unfinished representative files.
            continue
        representative = _read(path)
        if set(representative) != {"case_id", "method", "index", "search", "hardened"}:
            raise ValueError("Invalid representative fields")
        if (representative["case_id"], representative["method"], representative["index"]) != (*key, trial["index"]):
            raise ValueError("Representative does not use the declared best-case rule")
        for kind, field in (("search", "search"), ("hardened", "hardened")):
            op = next(op for op in operations if op["kind"] == kind and op["case_id"] == key[0]
                      and op["method"] == key[1] and op["index"] == trial["index"])
            saved_run = representative[field]
            if not isinstance(saved_run, dict) or not _same(
                    {key: value for key, value in saved_run.items() if key != "trace"}, replayed[op["id"]]):
                raise ValueError("Representative trace or metadata differs from replay")
            if trace_digest(saved_run["trace"]) != saved_run["trace_sha256"] or not _same(
                    evaluate_trace(saved_run["trace"], 0.75), saved_run["summary"]):
                raise ValueError("Representative trace fails independent monitor checks")
        representatives_checked += 1
    manifest_path = directory / "manifest.json"
    if final_manifest:
        manifest = _read(manifest_path)
        if set(manifest) != {"pilot_schema_version", "files"} or type(manifest["pilot_schema_version"]) is not int or manifest["pilot_schema_version"] != 1:
            raise ValueError("Invalid pilot manifest schema")
        if not isinstance(manifest["files"], dict) or set(manifest["files"]) != expected_files:
            raise ValueError("Manifest file set differs from completed evidence")
        for name, digest in manifest["files"].items():
            path = directory / name
            _read(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Pilot artifact hash mismatch")
    complete = journal["status"] == "COMPLETED" and final_manifest
    return {"status": "VERIFIED" if complete else "VERIFIED_INCOMPLETE",
            "pilot_status": journal["status"], "source_matches": source_matches,
            "manifest_present": final_manifest, "replayed_simulations": replay_count,
            "representatives_checked": representatives_checked,
            "representative_copies_pending": len(expected_representatives) - representatives_checked,
            "uncertain_simulation_attempts": expected_summary["counts"]["uncertain_simulation_attempts"],
            "provider_requests": 0, "cloud_jobs": 0,
            "scope": "Declared tuning tasks, deterministic candidate sequence, completed simulations, original traces, counts and aggregates; no model advantage or provider provenance authenticated."}
