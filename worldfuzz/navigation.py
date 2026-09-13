"""Bounded adapter to the unmodified PythonRobotics educational DWA planner.

Scenario/configuration imports are data only. The world is an ideal planar
unicycle with static point obstacles and a circular robot. No hardware, ROS,
perception, external programs, credentials or network services are involved.
The planner's chosen inflation radius is not the physical radius used by an
independent monitor. This adapter does not certify physical robot safety.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
from importlib import metadata
import json
import math
from pathlib import Path
from typing import Callable


UPSTREAM_COMMIT = "08b453a8e3b7a161b6fbbe6b73737758a8ec3482"
UPSTREAM_REPOSITORY = "https://github.com/AtsushiSakai/PythonRobotics"
SOURCE_SHA256 = "985aa9acb1e6de234fa15ec9c83d5a7435ce2d9e22c6f6e308e1ab038141c471"
FIXED_PARAMETERS = {
    "min_speed_mps": 0.0,
    "velocity_resolution_mps": 0.02,
    "yaw_resolution_rps": 0.05,
    "max_yaw_acceleration_rps2": 1.0,
    "initial_speed_mps": 0.0,
    "initial_yaw_rate_rps": 0.0,
}
PARAMETER_BOUNDS = {
    "max_speed_mps": (0.1, 1.5),
    "max_yaw_rate_rps": (0.2, 1.5),
    "max_accel_mps2": (0.2, 1.0),
    "predict_time_s": (0.5, 3.0),
    "to_goal_cost_gain": (0.01, 10.0),
    "speed_cost_gain": (0.01, 10.0),
    "obstacle_cost_gain": (0.01, 10.0),
    "planning_radius_m": (0.05, 2.0),
}
DEFAULT_PARAMETERS = {
    "max_speed_mps": 1.0,
    "max_yaw_rate_rps": 1.0,
    "max_accel_mps2": 0.8,
    "predict_time_s": 1.5,
    "to_goal_cost_gain": 0.5,
    "speed_cost_gain": 1.0,
    "obstacle_cost_gain": 0.5,
    "planning_radius_m": 0.35,
}


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8")).hexdigest()


def _object(value: object, required: set[str], name: str) -> dict:
    if type(value) is not dict or set(value) != required:
        raise ValueError(f"{name} requires exactly: {', '.join(sorted(required))}")
    return value


def _number(value: object, lower: float, upper: float, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(result) or not lower <= result <= upper:
        raise ValueError(f"{name} must be between {lower} and {upper}")
    return result


def _point(value: object, dimensions: int, name: str) -> list[float]:
    if type(value) is not list or len(value) != dimensions:
        raise ValueError(f"{name} requires {dimensions} numbers")
    return [_number(item, -math.pi if index == 2 else -20.0,
                    math.pi if index == 2 else 20.0, name)
            for index, item in enumerate(value)]


def default_scenario() -> dict:
    """Return a user-editable tuning example, not a customer incident."""
    return {"schema_version": 1, "start": [0.0, 0.0, 0.0],
            "goal": [4.0, 0.0], "obstacles": [[2.0, 0.65], [2.0, -0.65]],
            "robot_radius_m": 0.3, "goal_tolerance_m": 0.35,
            "horizon_s": 12.0, "dt_s": 0.1, "seed": 42}


def default_configuration(label: str = "Reference") -> dict:
    return validate_configuration({"controller": "pythonrobotics-dwa", "label": label,
                                   "parameters": dict(DEFAULT_PARAMETERS)})


def validate_scenario(scenario: dict) -> dict:
    """Strict data schema. Reject unsupported fields, types and unbounded work."""
    value = _object(scenario, {"schema_version", "start", "goal", "obstacles",
                              "robot_radius_m", "goal_tolerance_m", "horizon_s",
                              "dt_s", "seed"}, "scenario")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("scenario schema_version must be 1")
    if type(value["seed"]) is not int or not 0 <= value["seed"] <= 2**32 - 1:
        raise ValueError("seed must be an integer between 0 and 4294967295")
    obstacles = value["obstacles"]
    if type(obstacles) is not list or len(obstacles) > 40:
        raise ValueError("obstacles must be a list of at most 40 static points")
    normalized = {
        "schema_version": 1, "start": _point(value["start"], 3, "start"),
        "goal": _point(value["goal"], 2, "goal"),
        "obstacles": [_point(point, 2, "obstacle") for point in obstacles],
        "robot_radius_m": _number(value["robot_radius_m"], 0.05, 1.5, "robot_radius_m"),
        "goal_tolerance_m": _number(value["goal_tolerance_m"], 0.05, 1.0, "goal_tolerance_m"),
        "horizon_s": _number(value["horizon_s"], 0.1, 30.0, "horizon_s"),
        "dt_s": _number(value["dt_s"], 0.05, 0.2, "dt_s"), "seed": value["seed"],
    }
    steps = normalized["horizon_s"] / normalized["dt_s"]
    if steps > 300 + 1e-9 or not math.isclose(steps, round(steps), abs_tol=1e-8):
        raise ValueError("horizon_s / dt_s must be an integer from 1 through 300")
    return normalized


def validate_configuration(configuration: dict) -> dict:
    value = _object(configuration, {"controller", "label", "parameters"}, "configuration")
    if value["controller"] != "pythonrobotics-dwa":
        raise ValueError("Only the pinned pythonrobotics-dwa controller is supported")
    label = value["label"]
    if type(label) is not str or not 1 <= len(label.strip()) <= 64 or any(ord(c) < 32 for c in label):
        raise ValueError("label requires 1 through 64 printable characters")
    params = _object(value["parameters"], set(PARAMETER_BOUNDS), "parameters")
    return {"controller": "pythonrobotics-dwa", "label": label.strip(),
            "parameters": {key: _number(params[key], *bounds, key)
                           for key, bounds in PARAMETER_BOUNDS.items()}}


@lru_cache(maxsize=1)
def _planner():
    source = Path(__file__).with_name("vendor") / "pythonrobotics_dwa.py"
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("Pinned planner source differs from the recorded upstream file")
    try:
        from .vendor import pythonrobotics_dwa
    except ImportError as exc:
        raise RuntimeError("Navigation requires the optional dependencies: pip install '.[navigation]'") from exc
    pythonrobotics_dwa.show_animation = False
    return pythonrobotics_dwa


def controller_provenance(configuration: dict) -> dict:
    normalized = validate_configuration(configuration)
    versions = {}
    for package in ("numpy", "matplotlib"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = None
    return {"name": "PythonRobotics Dynamic Window Approach",
            "upstream_repository": UPSTREAM_REPOSITORY, "upstream_commit": UPSTREAM_COMMIT,
            "source_sha256": SOURCE_SHA256, "configuration_sha256": _digest(normalized),
            "fixed_parameters": dict(FIXED_PARAMETERS), "adapter_version": 1,
            "adapter_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "dependency_versions": versions,
            "world_model": "planar unicycle, circular robot, ideal static point obstacles",
            "seed_role": "recorded for scenario generation; planner itself uses no randomness"}


def simulate_navigation(scenario: dict, configuration: dict, *,
                        cancelled: Callable[[], bool] | None = None) -> dict:
    """Run trusted external controller code for <=300 steps, with boundary cancellation.

    No output safety verdict is accepted from the controller. Callers independently
    measure the returned trace against the physical scenario. Cancellation is
    checked before each planning step; callers can include a wall-clock deadline.
    """
    scenario = validate_scenario(scenario)
    configuration = validate_configuration(configuration)
    upstream = _planner()
    config = upstream.Config()
    params = configuration["parameters"]
    mapping = {"max_speed_mps": "max_speed", "max_yaw_rate_rps": "max_yaw_rate",
               "max_accel_mps2": "max_accel", "predict_time_s": "predict_time",
               "to_goal_cost_gain": "to_goal_cost_gain", "speed_cost_gain": "speed_cost_gain",
               "obstacle_cost_gain": "obstacle_cost_gain", "planning_radius_m": "robot_radius"}
    for external, internal in mapping.items():
        setattr(config, internal, params[external])
    config.min_speed = FIXED_PARAMETERS["min_speed_mps"]
    config.v_resolution = FIXED_PARAMETERS["velocity_resolution_mps"]
    config.yaw_rate_resolution = FIXED_PARAMETERS["yaw_resolution_rps"]
    config.max_delta_yaw_rate = FIXED_PARAMETERS["max_yaw_acceleration_rps2"]
    config.dt = scenario["dt_s"]
    config.robot_type = upstream.RobotType.circle
    # Upstream reduces min(obstacle distances) and does not accept an empty array.
    # A finite remote sentinel plus a zero weight makes the obstacle cost exactly
    # zero in the empty world, without changing the vendored algorithm.
    obstacles = scenario["obstacles"] or [[1_000_000.0, 1_000_000.0]]
    if not scenario["obstacles"]:
        config.obstacle_cost_gain = 0.0
    ob = upstream.np.array(obstacles, dtype=float)
    state = upstream.np.array(scenario["start"] + [0.0, 0.0], dtype=float)
    goal = upstream.np.array(scenario["goal"], dtype=float)
    trace = []
    status = "horizon_reached"
    steps = round(scenario["horizon_s"] / config.dt)
    for index in range(steps + 1):
        if not all(math.isfinite(float(item)) for item in state):
            raise RuntimeError("Controller produced non-finite state")
        trace.append(dict(zip(("t", "x", "y", "yaw", "v", "omega"),
                              (round(index * config.dt, 10), *(float(item) for item in state)))))
        if math.hypot(state[0] - goal[0], state[1] - goal[1]) <= scenario["goal_tolerance_m"]:
            status = "goal_reached"
            break
        if index == steps:
            break
        if cancelled is not None and cancelled():
            status = "cancelled"
            break
        control, _prediction = upstream.dwa_control(state, config, goal, ob)
        state = upstream.motion(state, control, config.dt)
    return {"schema_version": 1, "trace": trace, "status": status,
            "controller": controller_provenance(configuration), "scenario_sha256": _digest(scenario),
            "trace_sha256": _digest(trace), "steps_completed": len(trace) - 1}
