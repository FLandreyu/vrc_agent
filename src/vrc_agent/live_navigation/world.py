"""VRChat Home 专用现场配置；无通用地图 API、无客户端状态查询。"""

import hashlib
import json
from collections import Counter
from importlib.resources import files
from typing import Annotated, Literal

from pydantic import Field, model_validator

from vrc_agent.contracts import Contract, Identifier

Ratio = Annotated[float, Field(gt=0, le=1)]


class ArrivalRule(Contract):
    kind: Literal["box", "lower_half"]
    min_width: Ratio
    min_height: Ratio
    min_lower_coverage: Annotated[float, Field(ge=0, le=1)] = 0.0
    centre_tolerance: Annotated[float, Field(gt=0, le=0.15)] = 0.12
    status: Literal["ENGINEERING_INITIAL_FIELD_CHECK_REQUIRED"]

    @model_validator(mode="after")
    def valid_region(self):
        if self.kind == "lower_half" and self.min_lower_coverage <= 0:
            raise ValueError("lower-half arrival requires positive coverage")
        return self


class LiveLandmark(Contract):
    group: Literal["G1", "G2", "G3", "G4", "G5"]
    label: Identifier
    description: str
    arrival: ArrivalRule
    arrival_reference: str
    arrival_reference_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    recovery_zone: str


class LiveTask(Contract):
    task_id: Annotated[str, Field(pattern=r"^G[1-5]-[1-4]$")]
    group: Literal["G1", "G2", "G3", "G4", "G5"]
    start_position: str
    heading: str
    start_reference: str
    start_reference_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    condition: Literal["front", "left", "right", "partial_occlusion", "front_repeat"]
    replacement_reason: str = ""


class LiveWorld(Contract):
    schema_version: Literal[1]
    version: Literal["vrchat-home-live-v1"]
    world_id: Literal["wrld_4432ea9b-729c-46e3-8eaf-846aa0a37fdd"]
    avatar_id: Literal["avtr_4c2fb90f-478d-4f40-af12-467b33ee41cb"]
    instance: Literal["private"]
    client_version: Literal["2026.3.3p2"]
    frame_size: tuple[Literal[2160], Literal[1440]]
    calibration_source: str
    calibration_source_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    landmarks: tuple[LiveLandmark, ...]
    tasks: tuple[LiveTask, ...]

    @model_validator(mode="after")
    def fixed_twenty(self):
        groups = {f"G{i}" for i in range(1, 6)}
        if {item.group for item in self.landmarks} != groups or len(self.landmarks) != 5:
            raise ValueError("five calibrated landmarks required")
        if len({item.label for item in self.landmarks}) != 5:
            raise ValueError("landmark labels must be unique")
        expected = {f"{g}-{i}" for g in groups for i in range(1, 5)}
        if {t.task_id for t in self.tasks} != expected or len(self.tasks) != 20:
            raise ValueError("twenty frozen task IDs required")
        if Counter(t.group for t in self.tasks) != Counter({g: 4 for g in groups}):
            raise ValueError("four tasks per group required")
        for task in self.tasks:
            if not task.task_id.startswith(task.group + "-"):
                raise ValueError("task group mismatch")
            if task.condition == "front_repeat" and not task.replacement_reason:
                raise ValueError("replacement must be explicit")
        return self

    def task(self, task_id: str) -> LiveTask:
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        raise ValueError("unknown calibrated task")

    def landmark(self, group: str) -> LiveLandmark:
        return next(item for item in self.landmarks if item.group == group)

    def digest(self) -> str:
        raw = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


class LiveLandmarkV2(LiveLandmark):
    # 提示作为世界契约的一部分参与摘要，不能悄悄改变 v1 的视觉描述。
    prompt_addendum: Annotated[str, Field(max_length=2000)] = ""


class LiveWorldV2(LiveWorld):
    schema_version: Literal[2]
    version: Literal["vrchat-home-live-v2"]
    source_world_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    landmarks: tuple[LiveLandmarkV2, ...]

    @model_validator(mode="after")
    def valid_predecessor(self):
        if self.source_world_digest != load_live_world("v1").digest():
            raise ValueError("world v2 predecessor mismatch")
        return self


def load_live_world(version: Literal["v1", "v2"] = "v1") -> LiveWorld | LiveWorldV2:
    if version not in {"v1", "v2"}:
        raise ValueError("unknown live world version")
    root = files("vrc_agent.skills").joinpath("worlds")
    model = LiveWorld if version == "v1" else LiveWorldV2
    world = model.model_validate_json(
        root.joinpath(f"vrchat_home_live_{version}.json").read_text(encoding="utf-8")
    )
    expected = (
        root.joinpath(f"vrchat_home_live_{version}.sha256").read_text(encoding="ascii").strip()
    )
    if world.digest() != expected:
        raise ValueError("frozen live profile digest mismatch")
    return world
