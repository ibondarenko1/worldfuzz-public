"""Interactive, bounded paired searches. Separate from the frozen pilot protocol.

No model adapter or credential store is used here. Each requested scenario costs
two local simulations; replay verification is explicit and counted separately.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time

from .cases import TUNING_CASES
from .engine import json_bytes, source_fingerprint, write_json
from .pilot import candidates
from .schema import ScenarioSpec
from .simulator import evaluate_trace, simulate, trace_digest


TERMINAL = {"COMPLETED", "CANCELLED", "FAILED", "TIME_LIMIT_REACHED"}
POLICIES = {"baseline", "protective_distance"}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def read_json(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8_000_000:
        raise ValueError("Expected a bounded regular evidence file")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate evidence key")
            result[key] = value
        return result
    def invalid(_value):
        raise ValueError("Non-finite evidence number")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique, parse_constant=invalid)


def configuration(value):
    if not isinstance(value, dict) or set(value) != {"case_id", "method", "policy", "budget"}:
        raise ValueError("Choose a task, method, policy and scenario limit")
    case = next((c for c in TUNING_CASES if c.case_id == value["case_id"]), None)
    if case is None or value["method"] not in {"random", "crossing_time"} or value["policy"] not in POLICIES:
        raise ValueError("Unsupported task, method or policy")
    if type(value["budget"]) is not int or not 1 <= value["budget"] <= 48:
        raise ValueError("Scenario limit must be an integer from 1 to 48")
    return case


def finding(summary):
    return any(v["monitor"] == "human_distance" for v in summary["violations"])


def summarize(journal):
    completed = [op for op in journal["operations"] if op["status"] == "succeeded"]
    target = [op for op in completed if op["role"] == "target"]
    hardened = [op for op in completed if op["role"] == "hardened"]
    return {
        "completed_simulations": len(completed),
        "started_simulations": len(journal["operations"]),
        "unconfirmed_simulations": len(journal["operations"]) - len(completed),
        "scenario_pairs": len(hardened),
        "findings": sum(finding(op["summary"]) for op in target),
        "hardened_findings": sum(finding(op["summary"]) for op in hardened),
        "target_missions_completed": sum(op["summary"]["mission_completed"] for op in target),
        "hardened_missions_completed": sum(op["summary"]["mission_completed"] for op in hardened),
        "first_finding": next((op["trial"] for op in target if finding(op["summary"])), None),
    }


def selected_trial(operations):
    pairs = []
    for i in range(0, len(operations) - 1, 2):
        a, b = operations[i:i + 2]
        if a["status"] == b["status"] == "succeeded":
            pairs.append((a["summary"]["minimum_distance_m"], a["trial"]))
    return min(pairs)[1] if pairs else None


def new_experiment(out, config):
    case = configuration(config)
    directory = Path(out)
    directory.mkdir(parents=True, exist_ok=False)
    journal = {
        "experiment_schema_version": 1, "kind": "interactive_paired_search",
        "config": dict(config), "case": case.as_dict(),
        "limits": {"local_simulations": 2 * config["budget"], "wall_seconds": 30,
                   "provider_calls": 0, "cloud_jobs": 0, "automatic_retries": 0},
        "source_sha256": source_fingerprint(),
        "status": "QUEUED", "started_at": now(), "finished_at": None,
        "operations": [], "error": None,
    }
    journal["configuration_sha256"] = digest({k: journal[k] for k in ("config", "case", "limits")})
    journal["counts"] = summarize(journal)
    write_json(directory / "experiment.json", journal)
    return journal


def run_experiment(out, cancelled=lambda: False):
    directory = Path(out)
    journal = read_json(directory / "experiment.json")
    if journal["status"] != "QUEUED" or journal["operations"]:
        raise ValueError("Experiments are never resumed or automatically retried")
    case = configuration(journal["config"])
    if journal["source_sha256"] != source_fingerprint():
        raise ValueError("Source changed after the experiment was reserved")
    schedule = candidates(case, journal["config"]["method"])[:journal["config"]["budget"]]
    started = time.monotonic()
    best = None
    journal["status"] = "RUNNING"

    def save():
        journal["counts"] = summarize(journal)
        write_json(directory / "experiment.json", journal)

    try:
        save()
        for trial, scenario in enumerate(schedule, 1):
            pair = {"index": trial}
            for role, policy in (("target", journal["config"]["policy"]), ("hardened", "hardened")):
                if cancelled() or time.monotonic() - started >= journal["limits"]["wall_seconds"]:
                    journal["status"] = "CANCELLED" if cancelled() else "TIME_LIMIT_REACHED"
                    break
                op = {"trial": trial, "role": role, "policy": policy, "scenario": scenario.as_dict(),
                      "status": "pending", "started_at": now(), "finished_at": None}
                journal["operations"].append(op)
                save()  # Durable intent before every simulator call.
                run = simulate(scenario, policy, crossing_x_m=case.crossing_x_m)
                op.update(status="succeeded", finished_at=now(), summary=run["summary"],
                          trace_sha256=run["trace_sha256"], run_sha256=digest(run))
                pair[role] = run
                save()
            if journal["status"] != "RUNNING":
                break
            if best is None or pair["target"]["summary"]["minimum_distance_m"] < best:
                best = pair["target"]["summary"]["minimum_distance_m"]
                write_json(directory / "representative.json", pair)
        else:
            journal["status"] = "COMPLETED"
    except (Exception, KeyboardInterrupt):
        journal["status"] = "FAILED"
        journal["error"] = "Local execution stopped. Completed evidence was preserved."
        # Pending operations remain uncertain if an exception crossed the call.
    journal["finished_at"] = now()
    save()
    files = {"experiment.json": hashlib.sha256((directory / "experiment.json").read_bytes()).hexdigest()}
    if (directory / "representative.json").is_file():
        files["representative.json"] = hashlib.sha256((directory / "representative.json").read_bytes()).hexdigest()
    write_json(directory / "manifest.json", {"experiment_schema_version": 1, "files": files,
                                             "integrity": "Content hashes, not authenticated provenance"})
    return journal


def representative(directory, journal):
    path = Path(directory) / "representative.json"
    if not path.is_file():
        return None
    pair = read_json(path)
    index = selected_trial(journal["operations"])
    if pair.get("index") != index:
        return None  # A crash can leave an older representative copy.
    for role in ("target", "hardened"):
        op = next((o for o in journal["operations"] if o["trial"] == index and o["role"] == role), None)
        if op is None or op.get("run_sha256") != digest(pair.get(role)):
            return None
    return pair


class ReplayMeter:
    """Durable replay accounting also survives a mismatch or an exception."""
    def __init__(self, checkpoint=None):
        self.started = 0
        self.completed = 0
        self.checkpoint = checkpoint

    def receipt(self):
        return {"started_simulations": self.started, "local_simulations": self.completed,
                "unconfirmed_simulations": self.started - self.completed,
                "provider_calls": 0, "cloud_jobs": 0}

    def run(self, scenario, policy, crossing_x_m):
        self.started += 1
        if self.checkpoint:
            self.checkpoint(self.receipt())
        result = simulate(scenario, policy, crossing_x_m=crossing_x_m)
        self.completed += 1
        if self.checkpoint:
            self.checkpoint(self.receipt())
        return result


def check_run(run, meter):
    if not isinstance(run, dict) or run.get("policy") not in POLICIES | {"hardened"}:
        raise ValueError("Unsupported recorded policy")
    scenario = ScenarioSpec.from_dict(run["scenario"])
    if trace_digest(run["trace"]) != run["trace_sha256"]:
        raise ValueError("Recorded trace hash differs")
    if evaluate_trace(run["trace"], scenario.safety_distance_m) != run["summary"]:
        raise ValueError("Recorded monitor summary differs")
    replay = meter.run(scenario, run["policy"], run["world"]["human_crossing_x_m"])
    if digest(replay) != digest(run):
        raise ValueError("Simulator replay differs from the recording")
    return replay


def verify_pair(pair, *, meter=None):
    meter = meter or ReplayMeter()
    a, b = pair["target"], pair["hardened"]
    if digest(a["scenario"]) != digest(b["scenario"]) or digest(a["world"]) != digest(b["world"]) or b["policy"] != "hardened":
        raise ValueError("Comparison must use the same scenario and world")
    check_run(a, meter)
    check_run(b, meter)
    return {"status": "VERIFIED", "scope": "selected_recorded_pair", **meter.receipt(), "verified_at": now()}


def verify_experiment(out, *, meter=None):
    meter = meter or ReplayMeter()
    directory = Path(out)
    journal = read_json(directory / "experiment.json")
    if type(journal.get("experiment_schema_version")) is not int or journal["experiment_schema_version"] != 1 or journal.get("kind") != "interactive_paired_search":
        raise ValueError("Unsupported interactive experiment")
    expected_fields = {"experiment_schema_version", "kind", "config", "case", "limits", "source_sha256",
                       "status", "started_at", "finished_at", "operations", "error", "configuration_sha256", "counts"}
    if set(journal) != expected_fields:
        raise ValueError("Unexpected experiment fields")
    case = configuration(journal["config"])
    if digest(case.as_dict()) != digest(journal["case"]):
        raise ValueError("Fixed task was changed")
    expected_limits = {"local_simulations": 2 * journal["config"]["budget"], "wall_seconds": 30,
                       "provider_calls": 0, "cloud_jobs": 0, "automatic_retries": 0}
    if digest(journal["limits"]) != digest(expected_limits) or journal["configuration_sha256"] != digest(
            {k: journal[k] for k in ("config", "case", "limits")}):
        raise ValueError("Experiment limits or configuration differ")
    if journal["status"] not in TERMINAL | {"RUNNING", "QUEUED"}:
        raise ValueError("Unknown experiment state")
    schedule = candidates(case, journal["config"]["method"])[:journal["config"]["budget"]]
    operations = journal["operations"]
    def timestamp(value):
        if not isinstance(value, str):
            raise ValueError("Missing evidence timestamp")
        value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            raise ValueError("Evidence timestamp needs a timezone")
        return value
    started_at = timestamp(journal["started_at"])
    terminal = journal["status"] in TERMINAL
    finished_at = timestamp(journal["finished_at"]) if terminal else None
    if (finished_at and finished_at < started_at) or (not terminal and journal["finished_at"] is not None):
        raise ValueError("Inconsistent experiment timestamps")
    if (journal["status"] == "FAILED") != (isinstance(journal["error"], str) and bool(journal["error"])):
        raise ValueError("Inconsistent experiment error")
    if journal["status"] != "FAILED" and journal["error"] is not None:
        raise ValueError("Unexpected experiment error")
    if not isinstance(operations, list) or (journal["status"] == "QUEUED" and operations):
        raise ValueError("Queued experiment cannot contain simulations")
    sources = journal["source_sha256"]
    if (not isinstance(sources, dict) or not sources or any(
            not isinstance(k, str) or not k.endswith(".py") or not isinstance(v, str)
            or len(v) != 64 or any(c not in "0123456789abcdef" for c in v) for k, v in sources.items())):
        raise ValueError("Invalid source fingerprint")
    if len(operations) > expected_limits["local_simulations"]:
        raise ValueError("Simulation budget exceeded")
    # Check the complete structure and hashes before spending replay work.
    previous_time = started_at
    for i, op in enumerate(operations):
        role = "target" if i % 2 == 0 else "hardened"
        policy = journal["config"]["policy"] if role == "target" else "hardened"
        scenario = schedule[i // 2]
        if (op["trial"] != i // 2 + 1 or op["role"] != role or op["policy"] != policy
                or digest(op["scenario"]) != digest(scenario.as_dict())):
            raise ValueError("Operation schedule differs")
        basic = {"trial", "role", "policy", "scenario", "status", "started_at", "finished_at"}
        if type(op["trial"]) is not int or set(op) != basic | ({"summary", "trace_sha256", "run_sha256"} if op["status"] == "succeeded" else set()):
            raise ValueError("Unexpected operation fields")
        op_start = timestamp(op["started_at"])
        if op_start < previous_time or (finished_at and op_start > finished_at):
            raise ValueError("Operation timestamps are out of order")
        previous_time = op_start
        if op["status"] == "pending":
            if i != len(operations) - 1 or journal["status"] not in {"RUNNING", "FAILED"} or op["finished_at"] is not None:
                raise ValueError("Uncertain operation cannot precede completed work")
            continue
        if op["status"] != "succeeded":
            raise ValueError("Unknown operation state")
        op_finish = timestamp(op["finished_at"])
        if op_finish < op_start or (finished_at and op_finish > finished_at):
            raise ValueError("Operation completion timestamp differs")
        previous_time = op_finish
    if digest(summarize(journal)) != digest(journal["counts"]):
        raise ValueError("Recorded simulation or outcome counts differ")
    if journal["status"] == "COMPLETED" and journal["counts"]["completed_simulations"] != expected_limits["local_simulations"]:
        raise ValueError("Completed experiment is missing simulations")
    manifest_path = directory / "manifest.json"
    manifest_present = manifest_path.is_file()
    if manifest_present:
        manifest = read_json(manifest_path)
        names = set(manifest.get("files", {}))
        if type(manifest.get("experiment_schema_version")) is not int or manifest["experiment_schema_version"] != 1 or not {"experiment.json"} <= names <= {"experiment.json", "representative.json"}:
            raise ValueError("Unsupported evidence manifest")
        for name, expected in manifest["files"].items():
            path = directory / name
            read_json(path)
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise ValueError("Evidence file hash differs")
    selected = representative(directory, journal)
    if journal["status"] == "COMPLETED" and (selected is None or not manifest_present
            or "representative.json" not in manifest["files"]):
        raise ValueError("Completed experiment is missing its selected trace pair")
    for op in operations:
        if op["status"] != "succeeded":
            continue
        run = meter.run(ScenarioSpec.from_dict(op["scenario"]), op["policy"], case.crossing_x_m)
        if (digest(run["summary"]) != digest(op["summary"]) or run["trace_sha256"] != op["trace_sha256"]
                or digest(run) != op["run_sha256"]):
            raise ValueError("Recorded operation differs from replay")
    return {"status": "VERIFIED", "scope": "all_completed_experiment_operations",
            "experiment_status": journal["status"], "artifact_complete": manifest_present and selected is not None,
            **meter.receipt(), "unconfirmed_search_simulations": journal["counts"]["unconfirmed_simulations"],
            "source_matches": journal["source_sha256"] == source_fingerprint(),
            "provider_calls": 0, "cloud_jobs": 0, "verified_at": now()}
