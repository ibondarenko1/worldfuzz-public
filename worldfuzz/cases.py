"""Fixed experiment tasks, separate from the parameters a search can propose."""
from dataclasses import asdict, dataclass, fields
import math
import re

from .schema import ScenarioSpec


MUTABLE_FIELDS = frozenset({"human_crossing_time_s", "human_speed_mps",
                            "robot_speed_mps", "sensor_delay_ms", "occlusion"})


def _number(value, low, high, name):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be finite and in [{low}, {high}]")
    return float(value)


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    world_seed: int
    crossing_x_m: float
    split: str = "tuning"
    policy: str = "protective_distance"
    crossing_time_bounds: tuple = (1.0, 12.0)
    human_speed_bounds: tuple = (0.3, 1.5)
    robot_speed_bounds: tuple = (0.8, 1.2)
    sensor_delays_ms: tuple = (0,)
    occlusion_choices: tuple = (False,)
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported case schema")
        if not isinstance(self.case_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", self.case_id):
            raise ValueError("Invalid case identifier")
        if self.split not in ("tuning", "heldout"):
            raise ValueError("Case split must be tuning or heldout")
        if self.policy not in ("baseline", "protective_distance", "hardened"):
            raise ValueError("Unsupported case policy")
        if type(self.world_seed) is not int or not 0 <= self.world_seed <= 2**32 - 1:
            raise ValueError("Case world seed must be an unsigned 32-bit integer")
        object.__setattr__(self, "crossing_x_m", _number(self.crossing_x_m, 2, 10, "crossing_x_m"))
        for name, low, high in (("crossing_time_bounds", 1, 12),
                                ("human_speed_bounds", .3, 1.5),
                                ("robot_speed_bounds", .8, 1.2)):
            bounds = getattr(self, name)
            if not isinstance(bounds, (tuple, list)) or len(bounds) != 2:
                raise ValueError(f"{name} must contain two endpoints")
            bounds = tuple(_number(x, low, high, name) for x in bounds)
            if bounds[0] > bounds[1]:
                raise ValueError(f"{name} endpoints are reversed")
            object.__setattr__(self, name, bounds)
        for name in ("sensor_delays_ms", "occlusion_choices"):
            values = getattr(self, name)
            if not isinstance(values, (tuple, list)) or not values:
                raise ValueError(f"{name} must be a nonempty sequence")
            if name == "sensor_delays_ms":
                valid = all(type(x) is int and 0 <= x <= 1500 for x in values)
            else:
                valid = all(type(x) is bool for x in values)
            if not valid or len(set(values)) != len(values):
                raise ValueError(f"Invalid or duplicate {name}")
            object.__setattr__(self, name, tuple(values))

    @property
    def bounds(self):
        return {"human_crossing_time_s": self.crossing_time_bounds,
                "human_speed_mps": self.human_speed_bounds,
                "robot_speed_mps": self.robot_speed_bounds}

    @property
    def delay_choices(self):
        return self.sensor_delays_ms

    def scenario(self, **changes):
        if set(changes) - MUTABLE_FIELDS:
            raise ValueError("Proposal may change only declared case parameters")
        parameters = {name: sum(bounds) / 2 for name, bounds in self.bounds.items()}
        parameters.update(sensor_delay_ms=self.sensor_delays_ms[0],
                          occlusion=self.occlusion_choices[0])
        parameters.update(changes)
        scenario = ScenarioSpec(seed=self.world_seed, human_present=True,
                                safety_distance_m=.75, sensor_conflict=False,
                                command_source="operator", command_priority="normal", **parameters)
        self.validate_scenario(scenario)
        return scenario

    def validate_scenario(self, scenario):
        if not isinstance(scenario, ScenarioSpec):
            raise ValueError("Case requires a validated scenario")
        if (scenario.seed != self.world_seed or not scenario.human_present
                or scenario.safety_distance_m != .75 or scenario.sensor_conflict
                or scenario.command_source != "operator" or scenario.command_priority != "normal"):
            raise ValueError("Scenario changes a fixed case condition")
        for name, (low, high) in self.bounds.items():
            _number(getattr(scenario, name), low, high, name)
        if (scenario.sensor_delay_ms not in self.sensor_delays_ms
                or scenario.occlusion not in self.occlusion_choices):
            raise ValueError("Scenario uses a fault outside the case domain")

    def as_dict(self):
        return {name: list(value) if isinstance(value, tuple) else value
                for name, value in asdict(self).items()}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != {field.name for field in fields(cls)}:
            raise ValueError("Case record must contain its complete schema")
        return cls(**value)


# Only tuning tasks are instantiated. Held-out geometry and seeds remain unevaluated.
TUNING_CASES = (
    CaseSpec(case_id="tuning-clear-crossing", world_seed=1103, crossing_x_m=4.0),
    CaseSpec(case_id="tuning-interrupted-observation", world_seed=2207, crossing_x_m=6.0,
             sensor_delays_ms=(0, 150, 300), occlusion_choices=(False, True)),
)
