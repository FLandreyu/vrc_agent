"""限定世界的图像地标配置；不是地图、坐标或客户端状态查询接口。"""

from collections import Counter
from importlib.resources import files
from typing import Annotated, Literal

from pydantic import Field, model_validator

from vrc_agent.contracts import Contract, Identifier
from vrc_agent.planner import NavigationGoal


class Landmark(Contract):
    landmark_id: Identifier
    description: str
    goal: NavigationGoal
    color_bgr: tuple[Annotated[int, Field(ge=0, le=255)], ...]

    @model_validator(mode="after")
    def color_valid(self):
        if len(self.color_bgr) != 3 or self.color_bgr == (24, 24, 24):
            raise ValueError("landmark needs one non-background BGR color")
        return self


class NavigationLimits(Contract):
    arrival_frames: Annotated[int, Field(ge=2, le=4)] = 2
    centre_tolerance: Annotated[float, Field(gt=0, le=0.15)] = 0.10
    turn_progress: Annotated[float, Field(gt=0, le=0.1)] = 0.02
    stall_limit: Annotated[int, Field(ge=1, le=3)] = 3
    max_recoveries: Annotated[int, Field(ge=0, le=2)] = 2
    unknown_limit: Annotated[int, Field(ge=1, le=5)] = 3
    max_actions: Annotated[int, Field(ge=1, le=60)] = 40


class NavigationTask(Contract):
    task_id: Identifier
    landmark_id: Identifier
    start_view: str
    initial_centre: Annotated[float, Field(ge=0.2, le=0.8)]
    initial_size: Annotated[float, Field(ge=0.1, le=0.3)]
    fault: Literal["none", "temporary_block", "blocked", "takeover", "missing", "late"] = "none"


class WorldProfile(Contract):
    schema_version: Literal[1] = 1
    world_id: Literal["synthetic-first-world-v1"]
    mode: Literal["offline-only"]
    live_world_id: None = None
    calibration: Literal["SYNTHETIC_NOT_LIVE"]
    recovery_area: str
    limits: NavigationLimits = Field(default_factory=NavigationLimits)
    landmarks: tuple[Landmark, ...]
    tasks: tuple[NavigationTask, ...]

    @model_validator(mode="after")
    def fixed_suite(self):
        ids = [landmark.landmark_id for landmark in self.landmarks]
        labels = [landmark.goal.label for landmark in self.landmarks]
        colors = [landmark.color_bgr for landmark in self.landmarks]
        if len(ids) != 5 or any(len(set(values)) != 5 for values in (ids, labels, colors)):
            raise ValueError("first world requires five distinct landmarks, labels and colors")
        if len(self.tasks) != 20 or len({t.task_id for t in self.tasks}) != 20:
            raise ValueError("first-world denominator must contain 20 unique tasks")
        if Counter(t.landmark_id for t in self.tasks) != Counter({key: 4 for key in ids}):
            raise ValueError("exactly four tasks per landmark are required")
        return self

    def landmark(self, landmark_id: str) -> Landmark:
        return next(item for item in self.landmarks if item.landmark_id == landmark_id)


def load_first_world() -> WorldProfile:
    return WorldProfile.model_validate_json(
        files("vrc_agent.skills").joinpath("worlds/first_world.json").read_text(encoding="utf-8")
    )
