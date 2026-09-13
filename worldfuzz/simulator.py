"""Deterministic, intentionally limited warehouse motion model.

The robot and pedestrian are point centers. Distance means center-to-center
distance, not body clearance. The robot follows a straight path and can stop
instantly. A scripted pedestrian crosses that path. Sensor messages contain
ideal positions with explicit latency, missing observations and conflict flags.
The policy knows the fixture's pedestrian crossing direction: positive y with a
fixed x coordinate obtained from sensor observations. That declared trajectory
assumption permits reserving the crossing until the observed pedestrian passes.
This is a testable software policy demonstration, not validated robot physics,
perception, or a claim about a real robot's safety.

World state creates observations; policy decisions use only those observations,
the robot's own position, and the requested command. Monitors independently see
ground truth, including the continuous linear sweep between recorded samples.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from numbers import Real
import random
from typing import Any

from .schema import ScenarioSpec


TIMESTEP_S = 0.05
HORIZON_S = 20.0
GOAL_X_M = 12.0
# This fixed policy assumption is deliberately independent of the actual
# pedestrian's scenario speed (the scenario schema permits at most 1.5 m/s).
HUMAN_SPEED_BOUND_MPS = 2.0
FRESHNESS_LIMIT_S = 0.25


@dataclass(frozen=True)
class _Measurement:
    timestamp_s: float
    human_present: bool
    human_x: float | None
    human_y: float | None


@dataclass(frozen=True)
class _Observation:
    status: str
    age_s: float | None
    measurement: _Measurement | None
    sensor_conflict: bool


def trace_digest(trace: list[dict[str, Any]]) -> str:
    """Hash exact canonical JSON, with invalid non-finite numbers rejected."""
    payload = json.dumps(
        trace, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _perceived_distance(observation: _Observation, robot_x: float) -> float | None:
    measurement = observation.measurement
    if measurement is None or not measurement.human_present:
        return None
    return math.hypot(float(measurement.human_x) - robot_x, float(measurement.human_y))


def _decide(
    policy: str,
    observation: _Observation,
    robot_x: float,
    requested_speed: float,
    command_source: str,
    command_priority: str,
    safety_distance_m: float,
) -> tuple[float, str, bool]:
    """Choose motion without access to world trajectory or the scenario seed."""
    override_requested = command_priority == "override"
    override_accepted = override_requested and (
        policy == "baseline" or command_source == "operator"
    )
    if robot_x >= GOAL_X_M - 1e-9:
        return 0.0, "mission_completed", override_accepted

    perceived = _perceived_distance(observation, robot_x)
    if policy == "baseline":
        if override_accepted:
            return requested_speed, "priority_override", override_accepted
        if perceived is not None and perceived < safety_distance_m:
            return 0.0, "observed_person_nearby", override_accepted
        if observation.sensor_conflict:
            return requested_speed, "continues_despite_sensor_conflict", override_accepted
        if observation.status in {"occluded", "warming_up", "stale"}:
            return requested_speed, "continues_with_uncertain_observation", override_accepted
        return requested_speed, "route_clear", override_accepted

    if observation.sensor_conflict:
        return 0.0, "sensor_conflict_stop", override_accepted
    if observation.status in {"occluded", "warming_up"}:
        return 0.0, "observation_unavailable_stop", override_accepted
    if observation.measurement is None:
        return 0.0, "observation_unavailable_stop", override_accepted
    if perceived is None:
        if float(observation.age_s or 0.0) > FRESHNESS_LIMIT_S:
            return 0.0, "stale_clear_observation_stop", override_accepted
    else:
        # This fixture's crossing direction is known: the pedestrian travels
        # vertically in positive y and keeps the observed x coordinate. Reserve
        # that corridor until a received observation places the pedestrian past
        # it. Merely stopping on small Euclidean distance can leave a stationary
        # robot inside the pedestrian's future path. The guard uses the received
        # coordinates, never the hidden crossing time or actual current position.
        measurement = observation.measurement
        crossing_ahead_m = float(measurement.human_x) - robot_x
        crossing_clearance_m = safety_distance_m + 0.20
        stopping_line_m = crossing_clearance_m + requested_speed * TIMESTEP_S
        if (
            policy != "protective_distance"
            and 0.0 <= crossing_ahead_m <= stopping_line_m
            and float(measurement.human_y) < crossing_clearance_m
        ):
            return 0.0, "reserved_crossing_stop", override_accepted
        # A delayed location represents a disk of possible current positions.
        # This bound uses measured message age and a fixed motion bound. It
        # cannot recover exact current pedestrian coordinates from the world.
        uncertainty_m = HUMAN_SPEED_BOUND_MPS * float(observation.age_s or 0.0)
        next_step_motion_m = (HUMAN_SPEED_BOUND_MPS + requested_speed) * TIMESTEP_S
        protective_margin_m = 0.20 + next_step_motion_m
        conservative_distance = perceived - uncertainty_m
        if conservative_distance <= safety_distance_m + protective_margin_m:
            reason = (
                "stale_observation_protective_stop"
                if observation.status == "stale" else "person_protective_stop"
            )
            return 0.0, reason, override_accepted
    if override_requested and not override_accepted:
        return requested_speed, "guest_override_rejected_normal_route", False
    return requested_speed, "route_clear", override_accepted


def _closest_during_step(
    previous: dict[str, Any], current: dict[str, Any],
) -> tuple[float, float]:
    """Minimum separation and its time under piecewise linear motion."""
    start_x = float(previous["human_x"]) - float(previous["robot_x"])
    start_y = float(previous["human_y"]) - float(previous["robot_y"])
    end_x = float(current["human_x"]) - float(current["robot_x"])
    end_y = float(current["human_y"]) - float(current["robot_y"])
    delta_x, delta_y = end_x - start_x, end_y - start_y
    delta_squared = delta_x * delta_x + delta_y * delta_y
    fraction = 0.0 if delta_squared == 0.0 else max(
        0.0, min(1.0, -(start_x * delta_x + start_y * delta_y) / delta_squared),
    )
    distance = math.hypot(start_x + fraction * delta_x, start_y + fraction * delta_y)
    time_s = float(previous["t"]) + fraction * (float(current["t"]) - float(previous["t"]))
    return distance, time_s


def evaluate_trace(
    trace: list[dict[str, Any]], safety_distance_m: float = 0.75,
    goal_x_m: float = GOAL_X_M,
) -> dict[str, Any]:
    """Recompute monitor results from coordinates, ignoring reported distances.

    Empty or non-monotonic traces are invalid evidence. The caller is responsible
    for validating the trace schema and its correspondence to a scenario. A
    mission not completed is reported separately and is never a safety verdict.
    """
    if not trace:
        raise ValueError("Trace must contain at least one observation")
    if not math.isfinite(safety_distance_m) or safety_distance_m <= 0:
        raise ValueError("Safety distance must be positive and finite")
    violations: dict[str, dict[str, Any]] = {}
    minimum_distance = math.inf

    def add_violation(monitor: str, time_s: float, observed: Any, required: Any) -> None:
        if monitor not in violations:
            violations[monitor] = {
                "monitor": monitor,
                "time_s": round(time_s, 6),
                "observed": round(observed, 6) if isinstance(observed, float) else observed,
                "required": required,
            }

    previous: dict[str, Any] | None = None
    for row in trace:
        time_s = float(row["t"])
        for field in ("t", "robot_x", "robot_y", "speed"):
            if not math.isfinite(float(row[field])):
                raise ValueError(f"Trace field {field} must be finite")
        if previous is not None and time_s <= float(previous["t"]):
            raise ValueError("Trace times must be strictly increasing")
        if float(row["speed"]) < 0:
            raise ValueError("Trace speed must be non-negative")
        if row["human_present"]:
            for field in ("human_x", "human_y"):
                if row[field] is None or not math.isfinite(float(row[field])):
                    raise ValueError(f"Present human requires finite {field}")
            # Coordinates, not the convenience distance field, are evidence.
            distance = math.hypot(
                float(row["human_x"]) - float(row["robot_x"]),
                float(row["human_y"]) - float(row["robot_y"]),
            )
            closest_time = time_s
            if previous is not None and previous["human_present"]:
                swept_distance, swept_time = _closest_during_step(previous, row)
                if swept_distance < distance:
                    distance, closest_time = swept_distance, swept_time
            minimum_distance = min(minimum_distance, distance)
            if distance < safety_distance_m - 1e-9:
                add_violation("human_distance", closest_time, distance, safety_distance_m)
        if row["sensor_conflict"] and float(row["speed"]) > 1e-9:
            add_violation("sensor_conflict", time_s, float(row["speed"]), 0.0)
        if row["override_accepted"] and row["command_source"] != "operator":
            add_violation("unauthorized_command", time_s, row["command_source"], "operator")
        previous = row

    return {
        "minimum_distance_m": None if math.isinf(minimum_distance) else round(minimum_distance, 6),
        "mission_completed": any(float(row["robot_x"]) >= goal_x_m - 1e-6 for row in trace),
        "violations": sorted(violations.values(), key=lambda item: (item["time_s"], item["monitor"])),
        "steps": len(trace) - 1,
        "duration_s": round(float(trace[-1]["t"]) - float(trace[0]["t"]), 6),
    }


def simulate(
    scenario: ScenarioSpec,
    policy: str = "baseline",
    *,
    crossing_x_m: float | None = None,
) -> dict[str, Any]:
    """Run one fixed-horizon scenario and independently monitor its trace.

    Omitted geometry preserves the original seed-derived crossing. A fixed
    tuning task can instead supply a crossing in [2, 10] m; this world parameter
    reaches the policy only through sensor measurements. ``protective_distance``
    ablates only the hardened policy's reserved-crossing guard, retaining its
    observation freshness, distance uncertainty, conflict and command checks.
    """
    if policy not in {"baseline", "hardened", "protective_distance"}:
        raise ValueError("Policy must be 'baseline', 'hardened' or 'protective_distance'")
    if crossing_x_m is None:
        crossing_x = round(4.0 + random.Random(scenario.seed).uniform(-0.15, 0.15), 6)
    else:
        if (
            isinstance(crossing_x_m, bool)
            or not isinstance(crossing_x_m, Real)
            or not 2.0 <= crossing_x_m <= 10.0
            or not math.isfinite(crossing_x_m)
        ):
            raise ValueError("crossing_x_m must be a finite real number in [2, 10]")
        crossing_x = float(crossing_x_m)
    robot_x = 0.0
    history: list[_Measurement] = []
    latest_received: _Measurement | None = None
    trace: list[dict[str, Any]] = []
    total_steps = int(round(HORIZON_S / TIMESTEP_S))
    delay_s = scenario.sensor_delay_ms / 1000.0
    delivery_index = 0

    for step in range(total_steps + 1):
        time_s = round(step * TIMESTEP_S, 8)
        human_x = crossing_x if scenario.human_present else None
        human_y = (
            round(scenario.human_speed_mps * (time_s - scenario.human_crossing_time_s), 8)
            if scenario.human_present else None
        )
        # Fixed, observable sensor blackout. Its timing is not derived from the
        # pedestrian's hidden location. There are no queued detections inside it.
        capture_blocked = bool(scenario.occlusion and 2.0 <= time_s < 6.0)
        if not capture_blocked:
            history.append(_Measurement(time_s, scenario.human_present, human_x, human_y))
        while delivery_index < len(history) and history[delivery_index].timestamp_s <= time_s - delay_s + 1e-9:
            latest_received = history[delivery_index]
            delivery_index += 1
        age_s = None if latest_received is None else round(time_s - latest_received.timestamp_s, 8)
        conflict_active = bool(scenario.sensor_conflict and 2.5 <= time_s < 6.5)
        if capture_blocked:
            status = "occluded"
        elif latest_received is None:
            status = "warming_up"
        elif float(age_s) > FRESHNESS_LIMIT_S:
            status = "stale"
        else:
            status = "fresh"
        observation = _Observation(status, age_s, latest_received, conflict_active)
        speed, reason, override_accepted = _decide(
            policy, observation, robot_x, scenario.robot_speed_mps,
            scenario.command_source, scenario.command_priority, scenario.safety_distance_m,
        )
        # The last route segment can be shorter than one normal integration step.
        speed = min(speed, max(0.0, GOAL_X_M - robot_x) / TIMESTEP_S)
        if step == total_steps:
            speed = 0.0
            if robot_x < GOAL_X_M - 1e-9:
                reason = "horizon_reached"
        distance = None if not scenario.human_present else math.hypot(float(human_x) - robot_x, float(human_y))
        perceived_distance = _perceived_distance(observation, robot_x)
        trace.append({
            "t": time_s,
            "robot_x": round(robot_x, 8),
            "robot_y": 0.0,
            "human_x": human_x,
            "human_y": human_y,
            "human_present": scenario.human_present,
            "speed": round(speed, 8),
            "sensor_conflict": conflict_active,
            "override_accepted": override_accepted,
            "command_source": scenario.command_source,
            "distance_m": None if distance is None else round(distance, 8),
            "perceived_distance_m": None if perceived_distance is None else round(perceived_distance, 8),
            "action_reason": reason,
            "sensor_status": status,
            "sensor_age_s": age_s,
            "observed_human_x": None if latest_received is None else latest_received.human_x,
            "observed_human_y": None if latest_received is None else latest_received.human_y,
        })
        robot_x = min(GOAL_X_M, robot_x + speed * TIMESTEP_S)

    return {
        "policy": policy,
        "scenario": asdict(scenario),
        "world": {
            "model": "2d_point_center_kinematic",
            "timestep_s": TIMESTEP_S,
            "horizon_s": HORIZON_S,
            "goal_x_m": GOAL_X_M,
            "human_crossing_x_m": crossing_x,
            "human_speed_bound_mps": HUMAN_SPEED_BOUND_MPS,
            "pedestrian_direction_assumption": "positive_y_at_observed_fixed_x",
            "bounds": {"x_min": -1.0, "x_max": 13.0, "y_min": -4.0, "y_max": 4.0},
            "distance_convention": "center_to_center",
            "assumptions": [
                "Point centers; body dimensions and braking dynamics are not modeled.",
                "The robot stops instantaneously; the pedestrian follows a scripted straight path.",
                "The policy knows the pedestrian crosses in positive y at its measured fixed x coordinate.",
                "Sensor positions and fault status are idealized; latency and blackout are explicit.",
                "A successful replay is evidence only for this scenario and this toy model.",
            ],
        },
        "trace": trace,
        "trace_sha256": trace_digest(trace),
        "summary": evaluate_trace(trace, scenario.safety_distance_m),
    }
