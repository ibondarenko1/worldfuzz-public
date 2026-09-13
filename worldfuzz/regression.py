"""Bounded configuration comparisons on a pinned external navigation planner.

Projects are data, never executable instructions. Search, reduction and replay
are separately accounted for. This is simulation evidence, not certification.
"""
from copy import deepcopy
import hashlib
import io
import math
from pathlib import Path
import random
import re
import time
import zipfile

from .engine import json_bytes, write_json
from .experiment import digest, now, read_json
from .navigation import (default_configuration, default_scenario,
                         validate_configuration, validate_scenario,
                         simulate_navigation, controller_provenance, _digest as navigation_digest)


TERMINAL = {"COMPLETED", "CANCELLED", "TIME_LIMIT_REACHED", "FAILED"}


def _keys(value, expected, name):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise ValueError(f"Invalid {name} fields")


def _number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"Invalid {name}")
    return float(value)


def default_project():
    baseline = default_configuration("Reference")
    candidate = default_configuration("Proposed")
    candidate["parameters"]["planning_radius_m"] = .8
    return validate_project({"schema_version": 1, "name": "Protective radius update",
        "scenario": default_scenario(), "baseline": baseline, "candidate": candidate,
        "requirements": {"minimum_clearance_m": .05},
        "search": {"seed": 42, "budget": 4, "position_jitter_m": .15},
        "reduction": {"budget": 4}})


def validate_project(project):
    _keys(project, "schema_version name scenario baseline candidate requirements search reduction", "project")
    if type(project["schema_version"]) is not int or project["schema_version"] != 1:
        raise ValueError("Unsupported project version")
    if not isinstance(project["name"], str) or not 1 <= len(project["name"].strip()) <= 80:
        raise ValueError("Project name must have 1 to 80 characters")
    _keys(project["requirements"], "minimum_clearance_m", "requirements")
    _number(project["requirements"]["minimum_clearance_m"], 0, 2, "minimum clearance")
    _keys(project["search"], "seed budget position_jitter_m", "search")
    search = project["search"]
    if type(search["seed"]) is not int or not 0 <= search["seed"] < 2**32:
        raise ValueError("Search seed must be an unsigned 32-bit integer")
    if type(search["budget"]) is not int or not 1 <= search["budget"] <= 12:
        raise ValueError("Search budget must be 1 to 12 pairs")
    _number(search["position_jitter_m"], 0, 1, "obstacle position variation")
    _keys(project["reduction"], "budget", "reduction")
    if type(project["reduction"]["budget"]) is not int or not 0 <= project["reduction"]["budget"] <= 8:
        raise ValueError("Reduction budget must be 0 to 8 pairs")
    result = deepcopy(project)
    result["scenario"] = validate_scenario(project["scenario"])
    result["baseline"] = validate_configuration(project["baseline"])
    result["candidate"] = validate_configuration(project["candidate"])
    return result


def _source():
    root = Path(__file__).parent
    paths = [root / "regression.py", root / "navigation.py", root / "engine.py"]
    paths += sorted((root / "vendor").glob("*"))
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.is_file() and p.suffix != ".pyc"}


def _limits(project):
    return {"local_simulations": 2 * (project["search"]["budget"] + project["reduction"]["budget"]),
            "wall_seconds": 90, "provider_calls": 0, "cloud_jobs": 0, "automatic_retries": 0}


def scenario_schedule(project):
    rng = random.Random(project["search"]["seed"])
    for index in range(project["search"]["budget"]):
        scenario = deepcopy(project["scenario"])
        if index:
            jitter = project["search"]["position_jitter_m"]
            scenario["obstacles"] = [[round(max(-20, min(20, v + rng.uniform(-jitter, jitter))), 6)
                                      for v in point] for point in scenario["obstacles"]]
        yield validate_scenario(scenario)


def evaluate_navigation(trace, scenario, requirements):
    """Recompute geometric clearance and completion independently of planner costs."""
    scenario = validate_scenario(scenario)
    _keys(requirements, "minimum_clearance_m", "requirements")
    threshold = _number(requirements["minimum_clearance_m"], 0, 2, "minimum clearance")
    if not isinstance(trace, list) or not 1 <= len(trace) <= 301:
        raise ValueError("Expected a bounded navigation trace")
    closest, completion, previous = math.inf, None, None
    for index, row in enumerate(trace):
        _keys(row, "t x y yaw v omega", "trace row")
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in row.values()):
            raise ValueError("Trace values must be finite numbers")
        if abs(row["t"] - index * scenario["dt_s"]) > 1e-6:
            raise ValueError("Trace sampling does not match the scenario")
        if row["t"] > scenario["horizon_s"] + 1e-6:
            raise ValueError("Trace exceeds the mission horizon")
        if index == 0 and any(abs(row[k] - scenario["start"][i]) > 1e-6
                              for i, k in enumerate(("x", "y", "yaw"))):
            raise ValueError("Trace starts outside the declared state")
        if completion is None and math.hypot(row["x"] - scenario["goal"][0], row["y"] - scenario["goal"][1]) <= scenario["goal_tolerance_m"] + 1e-9:
            completion = row["t"]
        for ox, oy in scenario["obstacles"]:
            distance = math.hypot(row["x"] - ox, row["y"] - oy)
            if previous:
                dx, dy = row["x"] - previous["x"], row["y"] - previous["y"]
                length2 = dx * dx + dy * dy
                u = 0 if not length2 else max(0, min(1, ((ox - previous["x"]) * dx + (oy - previous["y"]) * dy) / length2))
                distance = min(distance, math.hypot(previous["x"] + u * dx - ox, previous["y"] + u * dy - oy))
            closest = min(closest, distance - scenario["robot_radius_m"])
        previous = row
    return {"minimum_clearance_m": None if math.isinf(closest) else round(closest, 6),
            "clearance_passed": closest >= threshold - 1e-9,
            "mission_completed": completion is not None,
            "completion_time_s": None if completion is None else round(completion, 6),
            "duration_s": round(trace[-1]["t"], 6)}


def regression_types(reference, proposed):
    result = []
    if reference["clearance_passed"] and not proposed["clearance_passed"]:
        result.append("NEW_CLEARANCE_VIOLATION")
    if reference["mission_completed"] and not proposed["mission_completed"]:
        result.append("NEW_MISSION_FAILURE")
    return result


def _counts(journal):
    done = [o for o in journal["operations"] if o["status"] == "succeeded"]
    pairs = [p for p in journal["pairs"] if p["phase"] == "search"]
    return {"started_simulations": len(journal["operations"]), "completed_simulations": len(done),
            "unconfirmed_simulations": sum(o["status"] == "pending" for o in journal["operations"]),
            "failed_simulations": sum(o["status"] == "failed" for o in journal["operations"]),
            "cancelled_simulations": sum(o["status"] == "cancelled" for o in journal["operations"]),
            "completed_pairs": len(pairs), "regression_pairs": sum(bool(p["regressions"]) for p in pairs),
            "search_simulations": sum(o["phase"] == "search" for o in journal["operations"]),
            "reduction_simulations": sum(o["phase"] == "reduction" for o in journal["operations"]),
            "provider_calls": 0, "cloud_jobs": 0}


def new_regression(out, project):
    project = validate_project(project)
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "runs").mkdir()
    write_json(directory / "project.json", project)
    journal = {"schema_version": 1, "kind": "external_navigation_configuration_comparison",
        "project_sha256": digest(project), "source_sha256": _source(), "limits": _limits(project),
        "status": "QUEUED", "started_at": now(), "finished_at": None,
        "operations": [], "pairs": [], "selected_pair": None, "error": None}
    journal["counts"] = _counts(journal)
    write_json(directory / "regression.json", journal)
    return journal


class _Stopped(Exception):
    def __init__(self, status):
        self.status = status


def run_regression(out, cancelled=lambda: False):
    directory = Path(out)
    project = validate_project(read_json(directory / "project.json"))
    journal = read_json(directory / "regression.json")
    if journal["status"] != "QUEUED" or journal["operations"]:
        raise ValueError("Existing experiments cannot be resumed or retried")
    if journal["project_sha256"] != digest(project) or journal["source_sha256"] != _source():
        raise ValueError("Project or source changed before execution")
    started = time.monotonic()
    journal["status"] = "RUNNING"

    def save():
        journal["counts"] = _counts(journal)
        write_json(directory / "regression.json", journal)

    def stop():
        return cancelled() or time.monotonic() - started >= journal["limits"]["wall_seconds"]

    def ensure_running():
        if stop():
            raise _Stopped("CANCELLED" if cancelled() else "TIME_LIMIT_REACHED")

    def pair(scenario, phase):
        item = {"phase": phase, "scenario": scenario, "operations": [], "regressions": []}
        summaries = []
        for role in ("baseline", "candidate"):
            ensure_running()
            if len(journal["operations"]) >= journal["limits"]["local_simulations"]:
                raise RuntimeError("Local simulation limit reached")
            index = len(journal["operations"])
            filename = f"runs/{index:04d}.json"
            op = {"index": index, "phase": phase, "role": role, "scenario": scenario,
                  "status": "pending", "run_file": filename, "run_sha256": None}
            journal["operations"].append(op)
            save()
            try:
                run = simulate_navigation(scenario, project[role], cancelled=stop)
                if run["status"] == "cancelled":
                    op["status"] = "cancelled"
                    save()
                    raise _Stopped("CANCELLED" if cancelled() else "TIME_LIMIT_REACHED")
                run["summary"] = evaluate_navigation(run["trace"], scenario, project["requirements"])
                if not run["summary"]["mission_completed"] and abs(run["trace"][-1]["t"] - scenario["horizon_s"]) > 1e-6:
                    raise ValueError("Incomplete navigation trace")
                write_json(directory / filename, run)
                op.update(status="succeeded", run_sha256=digest(run))
            except _Stopped:
                raise
            except Exception:
                op["status"] = "failed"
                save()
                raise
            item["operations"].append(index)
            summaries.append(run["summary"])
            save()
        item["regressions"] = regression_types(*summaries)
        journal["pairs"].append(item)
        save()
        return len(journal["pairs"]) - 1

    try:
        save()
        for scenario in scenario_schedule(project):
            index = pair(scenario, "search")
            if journal["selected_pair"] is None and journal["pairs"][index]["regressions"]:
                journal["selected_pair"] = index
                save()
        if journal["selected_pair"] is not None:
            selected = journal["pairs"][journal["selected_pair"]]
            required_types = selected["regressions"]
            current = deepcopy(selected["scenario"])
            attempts, obstacle = 0, 0
            while obstacle < len(current["obstacles"]) and attempts < project["reduction"]["budget"]:
                smaller = deepcopy(current)
                del smaller["obstacles"][obstacle]
                index = pair(smaller, "reduction")
                attempts += 1
                if journal["pairs"][index]["regressions"] == required_types:
                    current = smaller
                    journal["selected_pair"] = index
                    save()
                else:
                    obstacle += 1
    except _Stopped as exc:
        journal["status"] = exc.status
    except KeyboardInterrupt:
        journal["status"] = "CANCELLED"
        journal["error"] = "Interrupted. Pending work remains unconfirmed; no retry was made."
    except Exception:
        journal["status"] = "FAILED"
        journal["error"] = "Local comparison stopped. Completed evidence was preserved; no retry was made."
    else:
        journal["status"] = "COMPLETED"
    journal["finished_at"] = now()
    save()
    _manifest(directory, journal)
    return journal


def _evidence_names(journal):
    return ["project.json", "regression.json"] + [o["run_file"] for o in journal["operations"] if o["status"] == "succeeded"]


def _manifest(directory, journal):
    write_json(directory / "manifest.json", {"schema_version": 1,
        "files": {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
                  for name in _evidence_names(journal)},
        "integrity": "Content hashes, not authenticated provider or customer provenance"})


def _load(directory, *, require_manifest=True):
    directory = Path(directory)
    if directory.is_symlink() or (directory / "runs").is_symlink():
        raise ValueError("Expected regular evidence directories")
    project = validate_project(read_json(directory / "project.json"))
    journal = read_json(directory / "regression.json")
    _keys(journal, "schema_version kind project_sha256 source_sha256 limits status started_at finished_at operations pairs selected_pair error counts", "journal")
    if type(journal["schema_version"]) is not int or journal["schema_version"] != 1 or journal["kind"] != "external_navigation_configuration_comparison":
        raise ValueError("Unsupported regression evidence")
    if journal["project_sha256"] != digest(project) or journal["limits"] != _limits(project):
        raise ValueError("Project or execution limits differ")
    if journal["status"] not in TERMINAL | {"QUEUED", "RUNNING"}:
        raise ValueError("Invalid experiment status")
    if not isinstance(journal["operations"], list) or len(journal["operations"]) > _limits(project)["local_simulations"]:
        raise ValueError("Invalid operation count")
    if not isinstance(journal["pairs"], list):
        raise ValueError("Invalid pair list")
    runs = {}
    for index, op in enumerate(journal["operations"]):
        _keys(op, "index phase role scenario status run_file run_sha256", "operation")
        if type(op["index"]) is not int or op["index"] != index or op["run_file"] != f"runs/{index:04d}.json" or op["phase"] not in {"search", "reduction"}:
            raise ValueError("Invalid operation identity")
        if op["role"] not in {"baseline", "candidate"} or op["status"] not in {"pending", "succeeded", "failed", "cancelled"}:
            raise ValueError("Invalid operation state")
        scenario = validate_scenario(op["scenario"])
        fixed = {k: v for k, v in scenario.items() if k != "obstacles"}
        if fixed != {k: v for k, v in project["scenario"].items() if k != "obstacles"}:
            raise ValueError("Search changed fixed mission conditions")
        if op["status"] == "succeeded":
            run = read_json(directory / op["run_file"])
            _keys(run, "schema_version trace status controller scenario_sha256 trace_sha256 steps_completed summary", "navigation run")
            if type(run["schema_version"]) is not int or run["schema_version"] != 1:
                raise ValueError("Unsupported navigation run")
            if run["scenario_sha256"] != navigation_digest(scenario) or run["trace_sha256"] != navigation_digest(run["trace"]):
                raise ValueError("Trace is not bound to the declared scenario")
            if run["controller"] != controller_provenance(project[op["role"]]):
                raise ValueError("Controller source, configuration or environment differs")
            if type(run["steps_completed"]) is not int or run["steps_completed"] != len(run["trace"]) - 1:
                raise ValueError("Navigation step accounting differs")
            if digest(run) != op["run_sha256"] or evaluate_navigation(run["trace"], scenario, project["requirements"]) != run["summary"]:
                raise ValueError("Recorded trace or monitor differs")
            if run["status"] not in {"goal_reached", "horizon_reached"}:
                raise ValueError("A partial run cannot count as succeeded")
            if (run["status"] == "goal_reached") != run["summary"]["mission_completed"]:
                raise ValueError("Navigation termination and measured completion disagree")
            if not run["summary"]["mission_completed"] and abs(run["trace"][-1]["t"] - scenario["horizon_s"]) > 1e-6:
                raise ValueError("A trace ended before the mission horizon")
            runs[index] = run
    schedule = list(scenario_schedule(project))
    search_pairs, covered, reductions = 0, [], 0
    selected = None
    reduced_scenario, categories = None, None
    reduction_index = 0

    def expected_next():
        if search_pairs < len(schedule):
            return "search", schedule[search_pairs]
        if reduced_scenario is not None and reductions < project["reduction"]["budget"] and reduction_index < len(reduced_scenario["obstacles"]):
            smaller = deepcopy(reduced_scenario)
            del smaller["obstacles"][reduction_index]
            return "reduction", smaller
        return None, None

    for pair_index, item in enumerate(journal["pairs"]):
        _keys(item, "phase scenario operations regressions", "pair")
        ids = item["operations"]
        if not isinstance(ids, list) or len(ids) != 2 or any(type(i) is not int or i not in runs for i in ids) or ids != [len(covered), len(covered) + 1]:
            raise ValueError("Invalid completed pair operations")
        a, b = (journal["operations"][i] for i in ids)
        if a["role"] != "baseline" or b["role"] != "candidate" or a["scenario"] != b["scenario"] or a["scenario"] != item["scenario"] or a["phase"] != b["phase"] or item["phase"] != a["phase"]:
            raise ValueError("Comparison must preserve the same scenario and roles")
        if item["regressions"] != regression_types(runs[ids[0]]["summary"], runs[ids[1]]["summary"]):
            raise ValueError("Regression category differs")
        expected_phase, expected_scenario = expected_next()
        if item["phase"] != expected_phase or item["scenario"] != expected_scenario:
            raise ValueError("Search or reduction schedule differs")
        if item["phase"] == "search":
            if reductions or search_pairs >= len(schedule) or item["scenario"] != schedule[search_pairs]:
                raise ValueError("Search schedule differs")
            search_pairs += 1
            if selected is None and item["regressions"]:
                selected, reduced_scenario, categories = pair_index, item["scenario"], item["regressions"]
        else:
            reductions += 1
            if reduced_scenario is None or search_pairs != len(schedule) or reductions > project["reduction"]["budget"]:
                raise ValueError("Reduction lacks a completed search finding")
            before, after = reduced_scenario["obstacles"], item["scenario"]["obstacles"]
            if not any(before[:i] + before[i + 1:] == after for i in range(len(before))):
                raise ValueError("Reduction must remove one obstacle from the retained scenario")
            if item["regressions"] == categories:
                selected, reduced_scenario = pair_index, item["scenario"]
            else:
                reduction_index += 1
        covered.extend(ids)
    # A stop may preserve one completed baseline plus an unfinished candidate.
    remaining = journal["operations"][len(covered):]
    if len(remaining) > 2 or any(o["status"] != "succeeded" for o in remaining[:-1]):
        raise ValueError("Invalid partial pair")
    next_phase, next_scenario = expected_next()
    for role, op in zip(("baseline", "candidate"), remaining):
        if op["role"] != role or op["phase"] != next_phase or op["scenario"] != next_scenario:
            raise ValueError("Partial operation is outside the fixed schedule")
    if journal["status"] == "COMPLETED" and (remaining or next_phase is not None):
        raise ValueError("Completed experiment has missing operations")
    if journal["status"] == "QUEUED" and journal["operations"]:
        raise ValueError("Queued experiment has already executed operations")
    if journal["selected_pair"] is not None and type(journal["selected_pair"]) is not int:
        raise ValueError("Invalid selected pair index")
    if journal["selected_pair"] != selected or journal["counts"] != _counts(journal):
        raise ValueError("Selected case or accounting differs")
    if require_manifest:
        manifest = read_json(directory / "manifest.json")
        names = _evidence_names(journal)
        if set(manifest["files"]) != set(names):
            raise ValueError("Manifest file list differs")
        for name in names:
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != manifest["files"][name]:
                raise ValueError("Evidence content hash differs")
    return project, journal, runs


def get_regression(out):
    directory = Path(out)
    journal = read_json(directory / "regression.json")
    project = validate_project(read_json(directory / "project.json"))
    selected = None
    index = journal.get("selected_pair")
    # A no-finding result still shows a completed comparison.
    if index is None and journal["pairs"]:
        index = 0
    if index is not None:
        pair = journal["pairs"][index]
        selected = {"scenario": pair["scenario"], "regressions": pair["regressions"],
                    "reduced": pair["phase"] == "reduction",
                    "removed_obstacles": len(project["scenario"]["obstacles"]) - len(pair["scenario"]["obstacles"])}
        for role, op_index in zip(("baseline", "candidate"), pair["operations"]):
            selected[role] = read_json(directory / journal["operations"][op_index]["run_file"])
    verification = read_json(directory / "verification.json") if (directory / "verification.json").is_file() else None
    return {"project": project, "journal": journal, "selected": selected, "verification": verification}


def verify_regression(out, checkpoint=None):
    directory = Path(out)
    receipt = {"status": "RUNNING", "scope": "selected_regression_pair",
               "started_simulations": 0, "completed_simulations": 0,
               "provider_calls": 0, "cloud_jobs": 0, "regressions": [], "error": None}
    def save():
        write_json(directory / "verification.json", receipt)
        if checkpoint:
            checkpoint(deepcopy(receipt))
    save()
    try:
        project, journal, runs = _load(directory)
        if journal["status"] not in TERMINAL:
            raise ValueError("Stop the comparison before verification")
        if journal["source_sha256"] != _source():
            raise ValueError("Install the recorded implementation before exact replay")
        index = journal["selected_pair"]
        if index is None and journal["pairs"]:
            index = 0
        if index is not None:
            pair = journal["pairs"][index]
            for role, op_index in zip(("baseline", "candidate"), pair["operations"]):
                receipt["started_simulations"] += 1
                save()
                replay = simulate_navigation(pair["scenario"], project[role])
                replay["summary"] = evaluate_navigation(replay["trace"], pair["scenario"], project["requirements"])
                receipt["completed_simulations"] += 1
                save()
                if digest(replay) != digest(runs[op_index]):
                    raise ValueError("Navigation replay differs from recorded evidence")
            receipt["regressions"] = pair["regressions"]
        else:
            receipt["scope"] = "journal_without_completed_pair"
        receipt["status"] = "VERIFIED"
    except KeyboardInterrupt:
        receipt["status"] = "CANCELLED"
        receipt["error"] = "Verification interrupted. No retry was made."
    except Exception:
        receipt["status"] = "FAILED"
        receipt["error"] = "Evidence or exact replay did not match. No retry was made."
    receipt["verified_at"] = now()
    save()
    return receipt


def export_regression(out):
    directory = Path(out)
    project, journal, _ = _load(directory)
    if journal["status"] not in TERMINAL:
        raise ValueError("Stop the comparison before exporting")
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in _evidence_names(journal) + ["manifest.json"]:
            archive.writestr(name, (directory / name).read_bytes())
        if (directory / "verification.json").is_file():
            archive.writestr("verification.json", json_bytes(read_json(directory / "verification.json")))
        index = journal["selected_pair"]
        if index is None and journal["pairs"]:
            index = 0
        if index is not None:
            case = deepcopy(project)
            case["scenario"] = journal["pairs"][index]["scenario"]
            case["search"].update(budget=1, position_jitter_m=0)
            case["reduction"]["budget"] = 0
            archive.writestr("regression-case.json", json_bytes(case))
        archive.writestr("README.txt", "WorldFuzz external navigation configuration comparison\n\n"
            "Use the WorldFuzz source revision matching source_sha256 in regression.json.\n"
            "Install: python -m pip install '.[navigation]'\n"
            "Verify from your installed checkout: python -m worldfuzz regression-verify EXTRACTED_DIRECTORY\n"
            "Verification validates all recorded monitors and reruns the selected pair only (at most 2 local simulations).\n"
            "It does not replay every search/reduction attempt. VERIFIED means the evidence reproduced, not that the proposed configuration passed.\n"
            "To test a changed configuration on the saved case, copy regression-case.json, change candidate, and run:\n"
            "python -m worldfuzz regression --project PROJECT_JSON --out NEW_DIRECTORY\n"
            "regression-case.json is a derived single-case project when any pair completed. Edit only candidate to test another configuration.\n"
            "CLI exit status: 0 completed without new regression, 2 completed with regression, 1 execution error, 130 cancellation.\n"
            "The original two-policy crossing examples are separate. This bundle uses pinned PythonRobotics DWA sample code.\n"
            "No customer, upstream release regression, hardware validation or model-generated discovery is claimed.\n")
    return content.getvalue()
