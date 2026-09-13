"""Strict, portable experiment contracts. No generated code is executed."""
from dataclasses import asdict, dataclass, fields
import math


@dataclass(frozen=True)
class ScenarioSpec:
    seed: int = 18472
    human_present: bool = True
    human_crossing_time_s: float = 3.0
    human_speed_mps: float = 1.0
    sensor_delay_ms: int = 0
    occlusion: bool = False
    robot_speed_mps: float = 0.8
    sensor_conflict: bool = False
    command_source: str = "operator"
    command_priority: str = "normal"
    safety_distance_m: float = 0.75

    def __post_init__(self):
        for name in ("human_present", "occlusion", "sensor_conflict"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")
        for name, low, high in (("seed", 0, 2**32 - 1), ("sensor_delay_ms", 0, 1500)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"{name} must be an integer in [{low}, {high}]")
        for name, low, high in (
            ("human_crossing_time_s", 1.0, 12.0),
            ("human_speed_mps", 0.3, 1.5),
            ("robot_speed_mps", 0.3, 1.2),
            ("safety_distance_m", 0.75, 0.75),
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be finite and in [{low}, {high}]")
            object.__setattr__(self, name, float(value))
        if self.command_source not in ("operator", "guest"):
            raise ValueError("command_source must be operator or guest")
        if self.command_priority not in ("normal", "override"):
            raise ValueError("command_priority must be normal or override")

    def as_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("scenario must be an object")
        unknown = set(value) - {field.name for field in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown scenario fields: {sorted(unknown)}")
        return cls(**value)
