"""Durable, bounded experiment records for the synthetic simulator.

A pending entry is written before an operation: after a hard stop it records an
uncertain attempt, never permission to retry a possibly billed request.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import time

from .schema import ScenarioSpec


TARGETS = ("human_distance", "sensor_conflict", "unauthorized_command")
STATUSES = ("RUNNING", "FOUND", "NO_COUNTEREXAMPLE_FOUND", "MODEL_BUDGET_EXHAUSTED",
            "FAILED", "CANCELLED")
TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def _recorded_scenario(value):
    if not isinstance(value, dict) or set(value) != set(ScenarioSpec.__dataclass_fields__):
        raise ValueError("Recorded scenario must contain the complete fixed schema")
    return ScenarioSpec.from_dict(value)


def _valid_usage(value):
    return (isinstance(value, dict) and all(type(value.get(k)) is int and value[k] >= 0
                                          for k in TOKEN_KEYS)
            and value["total_tokens"] == value["prompt_tokens"] + value["completion_tokens"])


def _validate_provenance(provenance, state):
    if not isinstance(provenance, dict):
        raise ValueError("Invalid model provenance")
    if state == "pending":
        if provenance:
            raise ValueError("Pending model attempt has terminal provenance")
        return
    keys = {"request_state", "usage_state", "completion_request_attempted"}
    explicit = keys.intersection(provenance)
    if not explicit:
        # Mock/legacy planners can omit transport metadata; uncertainty remains.
        if "usage" in provenance and not _valid_usage(provenance["usage"]):
            raise ValueError("Invalid provider usage")
        return
    if explicit != keys:
        raise ValueError("Incomplete provider request-state metadata")
    request, usage_state, attempted = (provenance[k] for k in
                                     ("request_state", "usage_state", "completion_request_attempted"))
    if request == "not_sent":
        valid = attempted is False and usage_state == "not_applicable" and "usage" not in provenance
        if state == "succeeded":
            valid = False
    elif request in ("possibly_sent", "response_received"):
        valid = attempted is True and usage_state in ("known", "unknown")
        if usage_state == "known":
            valid = valid and request == "response_received" and _valid_usage(provenance.get("usage"))
        elif "usage" in provenance:
            valid = False
    else:
        valid = False
    if not valid:
        raise ValueError("Inconsistent provider request and usage states")


def usage_totals(attempts):
    """Sum only valid reported usage. Missing usage is unknown, not zero."""
    result = {"known_tokens": dict.fromkeys(TOKEN_KEYS, 0), "known_usage_attempts": 0,
              "unknown_usage_attempts": 0, "not_sent_attempts": 0}
    for attempt in attempts:
        provenance = attempt.get("provenance") or {}
        usage = provenance.get("usage")
        if _valid_usage(usage) and provenance.get("usage_state", "known") == "known":
            result["known_usage_attempts"] += 1
            for key in TOKEN_KEYS:
                result["known_tokens"][key] += usage[key]
        elif (provenance.get("request_state") == "not_sent"
              and provenance.get("completion_request_attempted") is False
              and provenance.get("usage_state") == "not_applicable"):
            result["not_sent_attempts"] += 1
        else:
            result["unknown_usage_attempts"] += 1
    return result


def _configuration(record):
    return {k: record[k] for k in ("strategy", "target", "seed", "budget", "limits", "planner_configuration")}


def _error(code, stage):
    # Exception strings can contain arbitrary provider text, credentials or paths.
    return {"code": code, "stage": stage,
            "message": "Campaign stopped; completed evidence was preserved and no retry was made."}


def _provenance(exc):
    from .nebius import NebiusError
    if isinstance(exc, (NebiusError, KeyboardInterrupt)):
        candidate = getattr(exc, "provenance", {})
        if isinstance(candidate, dict):
            return deepcopy(candidate)
    return {}


def _exception_code(exc, fallback):
    from .nebius import NebiusError
    if isinstance(exc, NebiusError):
        # NebiusError is the adapter's safe, finite error-code boundary.
        code = exc.code
        if isinstance(code, str) and code and len(code) <= 80 and all(
                c in "abcdefghijklmnopqrstuvwxyz0123456789_" for c in code):
            return code
    return fallback


def search(seed=42, budget=24, planner=None, target="human_distance", *,
           checkpoint=None, max_model_calls=None):
    from .engine import (has_violation, mission_feasibility, random_scenarios,
                         simulate, source_fingerprint)
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Search seed must be an unsigned 32-bit integer")
    if type(budget) is not int or not 1 <= budget <= 24:
        raise ValueError("Search budget must be an integer from 1 to 24")
    if target not in TARGETS:
        raise ValueError("Unsupported search target")
    if max_model_calls is None:
        max_model_calls = (budget + 7) // 8 if planner is not None else 0
    if type(max_model_calls) is not int or not 0 <= max_model_calls <= 24:
        raise ValueError("Model attempt budget must be an integer from 0 to 24")
    if planner is None:
        max_model_calls = 0
    # Inspect explicitly declared methods, not arbitrary dynamic attributes on a
    # proxy or mock. The CLI may replace its provider factory independently.
    has_adapter_contract = (callable(getattr(type(planner), "campaign_configuration", None))
                            and callable(getattr(type(planner), "proposal_token_limit", None)))
    if has_adapter_contract:
        planner_configuration = {"provider": "nebius_token_factory", **planner.campaign_configuration()}
    elif planner is not None:
        planner_configuration = {"provider": "unverified_planner", "model": None}
    else:
        planner_configuration = None
    started = time.monotonic()
    record = {"campaign_schema_version": 1, "strategy": "nemotron" if planner is not None else "random",
              "target": target, "seed": seed, "budget": budget, "executed_runs": 0,
              "history": [], "model_calls": [], "found": None, "elapsed_seconds": 0.0,
              "status": "RUNNING", "attempts": [], "rollout_attempts": [], "error": None,
              "started_at": _now(), "updated_at": None, "completed_at": None,
              "limits": {"simulation_runs": budget, "model_attempts": max_model_calls,
                         "max_batch_size": 8, "automatic_retries": 0},
              "planner_configuration": planner_configuration,
              "source_sha256": source_fingerprint()}
    record["configuration_sha256"] = _digest(_configuration(record))

    def emit():
        record["elapsed_seconds"] = round(time.monotonic() - started, 6)
        record["updated_at"] = _now()
        if record["status"] != "RUNNING":
            record["completed_at"] = record["updated_at"]
        record["usage"] = usage_totals(record["attempts"])
        record["execution_counts"] = {
            "completed_search_runs": len(record["history"]),
            "started_search_attempts": len(record["rollout_attempts"]),
            "unconfirmed_search_attempts": sum(a["status"] != "succeeded" for a in record["rollout_attempts"]),
            "model_attempts": len(record["attempts"])}
        if checkpoint is not None:
            checkpoint(deepcopy(record))

    emit()
    queue = [(scenario, None, index) for index, scenario in enumerate(
        random_scenarios(seed, budget), 1)] if planner is None else []
    seen = set()
    while record["executed_runs"] < budget:
        if not queue:
            if len(record["attempts"]) >= max_model_calls:
                record["status"] = "MODEL_BUDGET_EXHAUSTED"
                break
            count = min(8, budget - record["executed_runs"])
            attempt = {"index": len(record["attempts"]) + 1, "status": "pending",
                       "requested_count": count, "campaign_seed": (seed + len(record["attempts"])) % 2**32,
                       "history_count": len(record["history"]), "proposals": [], "provenance": {}, "error": None}
            attempt["request_limits"] = ({"max_completion_tokens": planner.proposal_token_limit(count),
                                           "timeout_seconds": planner_configuration["timeout_seconds"],
                                           "automatic_retries": 0}
                                          if has_adapter_contract else None)
            record["attempts"].append(attempt)
            emit()  # A failed checkpoint propagates before the model can run.
            try:
                proposals, provenance = planner.propose(deepcopy(record["history"]), count, attempt["campaign_seed"])
                if not isinstance(provenance, dict):
                    raise ValueError("Invalid planner provenance")
                # Ensure persisted metadata is plain JSON before running a scenario.
                json.dumps(provenance, allow_nan=False)
                attempt["provenance"] = deepcopy(provenance)
                if not isinstance(proposals, list) or len(proposals) != count:
                    raise ValueError("Invalid planner batch size")
                for scenario in proposals:
                    if (not isinstance(scenario, ScenarioSpec) or not scenario.human_present
                            or scenario.safety_distance_m != 0.75):
                        raise ValueError("Invalid or weakened scenario")
                    ScenarioSpec.from_dict(scenario.as_dict())
                attempt["proposals"] = [s.as_dict() for s in proposals]
                attempt["status"] = "succeeded"
                record["model_calls"].append(deepcopy(provenance))
                queue = [(scenario, attempt["index"], index) for index, scenario in enumerate(proposals, 1)]
            except KeyboardInterrupt as exc:
                attempt["provenance"] = _provenance(exc) or attempt["provenance"]
                attempt["status"] = "cancelled"
                attempt["error"] = record["error"] = _error("interrupted", "model")
                record["status"] = "CANCELLED"
            except Exception as exc:
                attempt["provenance"] = _provenance(exc) or attempt["provenance"]
                attempt["status"] = ("rejected" if isinstance(exc, (ValueError, TypeError))
                                     or attempt["provenance"].get("validation_state") == "rejected" else "failed")
                attempt["error"] = record["error"] = _error(_exception_code(exc, "planner_error"), "model")
                record["status"] = "FAILED"
            emit()
            if record["status"] != "RUNNING":
                break
        scenario, model_index, proposal_index = queue.pop(0)
        rollout = {"index": len(record["rollout_attempts"]) + 1, "status": "pending",
                   "scenario": scenario.as_dict(), "model_attempt_index": model_index,
                   "proposal_index": proposal_index, "history_index": None, "error": None}
        record["rollout_attempts"].append(rollout)
        emit()
        try:
            run = simulate(scenario, "baseline")
        except KeyboardInterrupt:
            rollout["status"] = "cancelled"
            rollout["error"] = record["error"] = _error("interrupted", "simulation")
            record["status"] = "CANCELLED"
        except Exception:
            rollout["status"] = "failed"
            rollout["error"] = record["error"] = _error("simulation_error", "simulation")
            record["status"] = "FAILED"
        else:
            identity = _digest(scenario.as_dict())
            duplicate = identity in seen
            seen.add(identity)
            record["history"].append({"index": len(record["history"]) + 1,
                                      "scenario": scenario.as_dict(), "summary": run["summary"],
                                      "trace_sha256": run["trace_sha256"], "duplicate": duplicate,
                                      "nominal_mission_feasible": mission_feasibility(scenario)["nominal_completion_possible"]})
            record["executed_runs"] = len(record["history"])
            rollout["status"] = "succeeded"
            rollout["history_index"] = record["executed_runs"]
            if has_violation(run, target):
                record["found"] = run
                record["status"] = "FOUND"
            elif record["executed_runs"] == budget:
                record["status"] = "NO_COUNTEREXAMPLE_FOUND"
        emit()
        if record["status"] != "RUNNING":
            break
    emit()
    return record


def verify_campaign(record):
    """Validate internal accounting and replay completed work without networking."""
    from .engine import (has_violation, mission_feasibility, random_scenarios,
                         simulate, source_fingerprint)
    if not isinstance(record, dict) or type(record.get("campaign_schema_version")) is not int or record["campaign_schema_version"] != 1:
        raise ValueError("Unsupported campaign schema")
    budget, seed, limits = record.get("budget"), record.get("seed"), record.get("limits")
    if (type(budget) is not int or not 1 <= budget <= 24 or type(seed) is not int
            or not 0 <= seed <= 2**32 - 1 or record.get("target") not in TARGETS
            or record.get("strategy") not in ("random", "nemotron")):
        raise ValueError("Invalid campaign configuration")
    if (not isinstance(limits, dict) or set(limits) != {"simulation_runs", "model_attempts", "max_batch_size", "automatic_retries"}
            or limits["simulation_runs"] != budget or limits["max_batch_size"] != 8
            or limits["automatic_retries"] != 0 or type(limits["model_attempts"]) is not int
            or not 0 <= limits["model_attempts"] <= 24):
        raise ValueError("Invalid campaign limits")
    planner_configuration = record.get("planner_configuration")
    if record["strategy"] == "random":
        if planner_configuration is not None:
            raise ValueError("Random search has unexpected planner configuration")
    elif not isinstance(planner_configuration, dict):
        raise ValueError("Missing planner configuration")
    elif planner_configuration.get("provider") == "nebius_token_factory":
        from .nebius import TIMEOUT_SECONDS, MAX_COMPLETION_TOKENS
        model = planner_configuration.get("model")
        if (set(planner_configuration) != {"provider", "model", "timeout_seconds", "max_completion_tokens", "automatic_retries"}
                or planner_configuration["timeout_seconds"] != TIMEOUT_SECONDS
                or planner_configuration["max_completion_tokens"] != MAX_COMPLETION_TOKENS
                or planner_configuration["automatic_retries"] != 0
                or (model is not None and (not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", model)))):
            raise ValueError("Invalid Nebius planner configuration")
    elif planner_configuration != {"provider": "unverified_planner", "model": None}:
        raise ValueError("Invalid unverified planner configuration")
    if record.get("configuration_sha256") != _digest(_configuration(record)):
        raise ValueError("Campaign configuration fingerprint mismatch")
    attempts, rollouts, history = (record.get(k) for k in ("attempts", "rollout_attempts", "history"))
    if any(not isinstance(value, list) for value in (attempts, rollouts, history)):
        raise ValueError("Campaign ledgers must be lists")
    if len(attempts) > limits["model_attempts"] or len(rollouts) > budget or len(history) > len(rollouts):
        raise ValueError("Campaign exceeded recorded limits")
    if type(record.get("executed_runs")) is not int or record["executed_runs"] != len(history):
        raise ValueError("Campaign run accounting mismatch")
    if record.get("strategy") == "random" and (attempts or limits["model_attempts"] != 0):
        raise ValueError("Random campaign cannot contain model attempts")
    expected_counts = {"completed_search_runs": len(history), "started_search_attempts": len(rollouts),
                       "unconfirmed_search_attempts": sum(a.get("status") != "succeeded" for a in rollouts),
                       "model_attempts": len(attempts)}
    if record.get("execution_counts") != expected_counts:
        raise ValueError("Campaign attempt accounting mismatch")
    successful_calls = []
    available = []
    terminal = []
    for index, attempt in enumerate(attempts, 1):
        if not isinstance(attempt, dict) or type(attempt.get("index")) is not int or attempt["index"] != index:
            raise ValueError("Model attempt index mismatch")
        previous_count = sum(a.get("requested_count", 0) for a in attempts[:index - 1])
        if (any(type(attempt.get(k)) is not int for k in ("history_count", "requested_count", "campaign_seed"))
                or attempt.get("history_count") != previous_count
                or attempt.get("requested_count") != min(8, budget - previous_count)
                or attempt.get("campaign_seed") != (seed + index - 1) % 2**32
                or previous_count > len(history)):
            raise ValueError("Model attempt order or proposal budget mismatch")
        state, proposals = attempt.get("status"), attempt.get("proposals")
        if state not in ("pending", "succeeded", "failed", "rejected", "cancelled") or not isinstance(proposals, list):
            raise ValueError("Invalid model attempt state")
        provenance = attempt.get("provenance")
        _validate_provenance(provenance, state)
        if planner_configuration["provider"] == "nebius_token_factory":
            from .nebius import NemotronPlanner
            request_limits = {"max_completion_tokens": NemotronPlanner.proposal_token_limit(attempt["requested_count"]),
                              "timeout_seconds": planner_configuration["timeout_seconds"], "automatic_retries": 0}
            if attempt.get("request_limits") != request_limits:
                raise ValueError("Predeclared completion request limits mismatch")
            if ("max_completion_tokens" in provenance and
                    provenance["max_completion_tokens"] != request_limits["max_completion_tokens"]):
                raise ValueError("Provider request differs from the declared token cap")
            if "model" in provenance and provenance["model"] != planner_configuration["model"]:
                raise ValueError("Provider model differs from the campaign configuration")
            if (state == "succeeded" and provenance.get("usage_state") == "known"
                    and provenance["usage"]["completion_tokens"] > request_limits["max_completion_tokens"]):
                raise ValueError("Successful completion exceeded its declared token cap")
        elif attempt.get("request_limits") is not None:
            raise ValueError("Unverified planner cannot assert Nebius request limits")
        if state == "succeeded":
            if len(proposals) != attempt["requested_count"] or attempt.get("error") is not None:
                raise ValueError("Successful model batch mismatch")
            successful_calls.append(provenance)
            for proposal_index, proposal in enumerate(proposals, 1):
                scenario = _recorded_scenario(proposal)
                if not scenario.human_present or scenario.safety_distance_m != 0.75:
                    raise ValueError("Campaign proposal weakens the fixed claim")
                available.append((proposal, index, proposal_index))
        else:
            if index != len(attempts) or proposals or (state == "pending" and attempt.get("error") is not None):
                raise ValueError("Campaign continued after an unfinished model attempt")
            terminal.append((state, attempt.get("error"), "model"))
    if record.get("model_calls") != successful_calls:
        raise ValueError("Campaign successful model provenance mismatch")
    if record.get("usage") != usage_totals(attempts):
        raise ValueError("Campaign cumulative usage mismatch")
    if record["strategy"] == "random":
        available = [(s.as_dict(), None, i) for i, s in enumerate(random_scenarios(seed, budget), 1)]
    if len(rollouts) > len(available):
        raise ValueError("Campaign executed an unproposed scenario")
    completed = 0
    for index, rollout in enumerate(rollouts, 1):
        if not isinstance(rollout, dict) or type(rollout.get("index")) is not int or rollout["index"] != index:
            raise ValueError("Rollout index mismatch")
        expected_scenario, model_index, proposal_index = available[index - 1]
        if (rollout.get("scenario") != expected_scenario or rollout.get("model_attempt_index") != model_index
                or rollout.get("proposal_index") != proposal_index):
            raise ValueError("Rollout does not match the declared proposal order")
        state = rollout.get("status")
        if state == "succeeded":
            completed += 1
            if (rollout.get("history_index") != completed or rollout.get("error") is not None
                    or completed > len(history) or history[completed - 1].get("scenario") != expected_scenario):
                raise ValueError("Completed rollout history mismatch")
        elif state in ("pending", "failed", "cancelled"):
            if index != len(rollouts) or rollout.get("history_index") is not None:
                raise ValueError("Campaign continued after unfinished simulation")
            if state == "pending" and rollout.get("error") is not None:
                raise ValueError("Pending simulation has a terminal error")
            terminal.append((state, rollout.get("error"), "simulation"))
        else:
            raise ValueError("Invalid simulation attempt state")
    if completed != len(history) or len(terminal) > 1:
        raise ValueError("Campaign history or unfinished operation mismatch")
    seen, last_run, finding_indices = set(), None, []
    for index, item in enumerate(history, 1):
        scenario = _recorded_scenario(item["scenario"])
        last_run = simulate(scenario, "baseline")
        identity = _digest(scenario.as_dict())
        if (item.get("index") != index or item.get("summary") != last_run["summary"]
                or item.get("trace_sha256") != last_run["trace_sha256"]
                or item.get("duplicate") is not (identity in seen)
                or item.get("nominal_mission_feasible") is not mission_feasibility(scenario)["nominal_completion_possible"]):
            raise ValueError("Campaign history replay mismatch")
        seen.add(identity)
        if has_violation(last_run, record["target"]):
            finding_indices.append(index)
    state = record.get("status")
    if state not in STATUSES or finding_indices not in ([], [len(history)]):
        raise ValueError("Campaign continued after a finding or has an invalid state")
    if record.get("found") != (last_run if finding_indices else None):
        raise ValueError("Campaign finding evidence mismatch")
    if finding_indices:
        expected = "FOUND"
        if terminal or len(rollouts) != len(history):
            raise ValueError("Campaign continued after finding")
    elif terminal:
        operation_state, error, stage = terminal[0]
        expected = {"pending": "RUNNING", "failed": "FAILED", "rejected": "FAILED", "cancelled": "CANCELLED"}[operation_state]
        if operation_state != "pending" and (not isinstance(error, dict) or error.get("stage") != stage
                                             or record.get("error") != error):
            raise ValueError("Campaign failure evidence mismatch")
    elif len(history) == budget:
        expected = "NO_COUNTEREXAMPLE_FOUND"
    elif record["strategy"] == "nemotron" and len(attempts) == limits["model_attempts"] and len(rollouts) == len(available):
        expected = "MODEL_BUDGET_EXHAUSTED"
    else:
        expected = "RUNNING"
    # A checkpoint between the last allowed operation and the terminal write is
    # still incomplete. It may not be promoted to a completed verdict by readers.
    if state != expected and not (state == "RUNNING" and expected == "MODEL_BUDGET_EXHAUSTED"):
        raise ValueError("Campaign status does not match recorded operations")
    if state not in ("FAILED", "CANCELLED") and record.get("error") is not None:
        raise ValueError("Unexpected campaign error")
    try:
        start, updated = (datetime.fromisoformat(record[k]) for k in ("started_at", "updated_at"))
        if start.tzinfo is None or updated.tzinfo is None or updated < start:
            raise ValueError("Invalid campaign timestamps")
        if state == "RUNNING":
            if record.get("completed_at") is not None:
                raise ValueError("Running campaign has completion timestamp")
        elif record.get("completed_at") != record["updated_at"]:
            raise ValueError("Terminal campaign timestamp mismatch")
    except (TypeError, KeyError) as exc:
        raise ValueError("Invalid campaign timestamps") from exc
    if type(record.get("elapsed_seconds")) not in (int, float) or not math.isfinite(record["elapsed_seconds"]) or record["elapsed_seconds"] < 0:
        raise ValueError("Invalid campaign elapsed time")
    return {"status": "VERIFIED", "result": "INCOMPLETE" if state == "RUNNING" else state,
            "campaign_result": state, "replayed_search_runs": len(history),
            "usage": record["usage"], "execution_counts": expected_counts,
            "source_matches": record.get("source_sha256") == source_fingerprint(),
            "model_provenance_authenticated": False,
            "verification_scope": "completed search replays, fixed claims, proposal order, recorded limits, attempt and usage accounting; provider provenance is unauthenticated"}
