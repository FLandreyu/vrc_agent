"""v1 契约。模型只能构造 Proposal，权限和时钟字段由可信运行时添加。"""

from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    model_validator,
)

IDENTIFIER_PATTERN = r"^[A-Za-z0-9_.:-]+$"
MAX_IDENTIFIER_LENGTH = 128
Identifier = Annotated[
    str,
    Field(min_length=1, max_length=MAX_IDENTIFIER_LENGTH, pattern=IDENTIFIER_PATTERN),
]
NanoTime = Annotated[int, Field(ge=0)]
Scalar = StrictBool | StrictInt | StrictFloat
Axis = Annotated[float, Field(ge=-1.0, le=1.0)]


def new_id() -> str:
    return uuid4().hex


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, allow_inf_nan=False)


class Event(Contract):
    schema_version: Literal[1] = 1
    event_id: Identifier = Field(default_factory=new_id)
    session_id: Identifier


class ParameterSample(Contract):
    value: Scalar
    received_at_ns: NanoTime


class Observation(Event):
    observation_id: Identifier = Field(default_factory=new_id)
    captured_at_ns: NanoTime
    environment_epoch: Annotated[int, Field(ge=0)]
    frame_ref: Identifier | None = None
    frame_size: tuple[Annotated[int, Field(gt=0)], Annotated[int, Field(gt=0)]] | None = None
    transcript: Annotated[str, Field(max_length=8192)] | None = None
    avatar_parameters: dict[str, ParameterSample] = Field(default_factory=dict)
    window_state: Literal["visible", "lost", "minimized", "unknown"] = "unknown"


class Entity(Contract):
    track_id: Identifier
    label: Annotated[str, Field(max_length=128)]
    bbox: tuple[float, float, float, float]
    confidence: Annotated[float, Field(ge=0, le=1)]
    evidence_observation_id: Identifier
    last_seen_ns: NanoTime

    @model_validator(mode="after")
    def valid_box(self):
        x1, y1, x2, y2 = self.bbox
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("bbox must be an ordered normalized rectangle")
        return self


class SceneState(Event):
    scene_id: Identifier = Field(default_factory=new_id)
    observation_ids: tuple[Identifier, ...]
    generated_at_ns: NanoTime
    environment_epoch: Annotated[int, Field(ge=0)]
    entities: tuple[Entity, ...] = ()
    hazards: tuple[str, ...] = ()
    uncertainties: tuple[str, ...] = ()
    active_goal: str | None = None
    #: 场景整体可信度：None 表示**无法判断**（看不清、没有证据或被上游降级），
    #: 此时 entities 必须为空——不允许在「不知道」的同时声称看到了东西。
    confidence: Annotated[float, Field(ge=0, le=1)] | None = None

    @model_validator(mode="after")
    def valid_confidence(self):
        if self.confidence is None and self.entities:
            raise ValueError("a scene without confidence must not contain entities")
        return self


class MotionArgs(Contract):
    forward: Axis = 0.0
    strafe: Axis = 0.0
    yaw: Axis = 0.0
    duration_ms: Annotated[int, Field(ge=1, le=500)]


class EmptyArgs(Contract):
    pass


class WaitArgs(Contract):
    duration_ms: Annotated[int, Field(ge=1, le=5000)]


class ChatArgs(Contract):
    text: Annotated[str, Field(min_length=1, max_length=144)]

    @model_validator(mode="after")
    def valid_text(self):
        # 保守使用 UTF-16 code units，避免客户端把非 BMP 字符计为两个单元。
        if "\x00" in self.text or len(self.text.encode("utf-16-le")) // 2 > 144:
            raise ValueError("chat text exceeds 144 UTF-16 units or contains NUL")
        if self.text.count("\n") > 8:
            raise ValueError("chat text exceeds nine explicit lines")
        return self


class AvatarArgs(Contract):
    parameter: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_]+$")]
    value: Scalar


class SpeakArgs(Contract):
    utterance_id: Identifier


class InteractArgs(Contract):
    skill_id: Identifier
    target_track_id: Identifier | None = None


ARG_TYPES = {
    "motion": MotionArgs,
    "stop": EmptyArgs,
    "wait": WaitArgs,
    "chatbox": ChatArgs,
    "avatar_set": AvatarArgs,
    "speak": SpeakArgs,
    "interact": InteractArgs,
}
Kind = Literal["motion", "stop", "wait", "chatbox", "avatar_set", "speak", "interact"]


class ActionProposal(Contract):
    kind: Kind
    args: MotionArgs | EmptyArgs | WaitArgs | ChatArgs | AvatarArgs | SpeakArgs | InteractArgs
    expected_outcome: Annotated[str, Field(max_length=256)] = ""

    @model_validator(mode="before")
    @classmethod
    def typed_args(cls, data):
        if isinstance(data, dict) and data.get("kind") in ARG_TYPES:
            data = dict(data)
            data["args"] = ARG_TYPES[data["kind"]].model_validate(data.get("args", {}))
        return data

    @model_validator(mode="after")
    def matching_args(self):
        if type(self.args) is not ARG_TYPES[self.kind]:
            raise ValueError("argument type does not match action kind")
        return self


class ActionRequest(ActionProposal, Event):
    action_id: Identifier = Field(default_factory=new_id)
    lease_id: Identifier
    environment_epoch: Annotated[int, Field(ge=0)]
    based_on_observation_id: Identifier
    issued_at_ns: NanoTime
    expires_at_ns: NanoTime

    @model_validator(mode="after")
    def valid_expiry(self):
        if self.expires_at_ns <= self.issued_at_ns:
            raise ValueError("expires_at_ns must follow issued_at_ns")
        return self


Status = Literal["REJECTED", "ACCEPTED", "RUNNING", "COMPLETED", "CANCELLED", "TIMED_OUT", "FAILED"]
Reason = Literal[
    "OK",
    "INVALID_ARGS",
    "UNSUPPORTED_CAPABILITY",
    "STALE_OBSERVATION",
    "EXPIRED_LEASE",
    "EPOCH_MISMATCH",
    "DUPLICATE_ACTION",
    "PREEMPTED",
    "HEARTBEAT_LOST",
    "TRANSPORT_ERROR",
    "NO_PROGRESS",
    "BUDGET_EXCEEDED",
    "NOT_ARMED",
    "BUSY",
    "SESSION_MISMATCH",
    "EXPIRED_ACTION",
    "FOCUS_LOST",
    "MANUAL_STOP",
    "RATE_LIMITED",
    "CAPACITY_REACHED",
]

#: 顺序稳定，供遥测错误码登记表与文档枚举；新增取值时必须同步 ARCHITECTURE 与遥测登记表。
REASONS: tuple[Reason, ...] = (
    "OK",
    "INVALID_ARGS",
    "UNSUPPORTED_CAPABILITY",
    "STALE_OBSERVATION",
    "EXPIRED_LEASE",
    "EPOCH_MISMATCH",
    "DUPLICATE_ACTION",
    "PREEMPTED",
    "HEARTBEAT_LOST",
    "TRANSPORT_ERROR",
    "NO_PROGRESS",
    "BUDGET_EXCEEDED",
    "NOT_ARMED",
    "BUSY",
    "SESSION_MISMATCH",
    "EXPIRED_ACTION",
    "FOCUS_LOST",
    "MANUAL_STOP",
    "RATE_LIMITED",
    "CAPACITY_REACHED",
)


class ActionResult(Event):
    action_id: Identifier
    status: Status
    reason_code: Reason = "OK"
    started_at_ns: NanoTime | None = None
    finished_at_ns: NanoTime | None = None
    evidence_observation_ids: tuple[Identifier, ...] = ()
    verification: Literal["UNVERIFIED", "SUCCESS", "FAILURE", "UNKNOWN"] = "UNVERIFIED"
