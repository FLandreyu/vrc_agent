"""T05 契约交叉验证：感知层交给下游的只能是「受约束的描述」，不是动作、不是画面、不是密钥。

这份文件按 ADR-009 由**实现者以外**的执行者编写，刻意不复用 `perception/` 内部的判定表
（`ACTION_LIKE_KEYS`、`build_scene` 的分支条件、`DEFAULT_FIXTURE` 的期望序列），而是把期望值
从 `docs/ARCHITECTURE.md` 第 3、4、6、7 节与 `src/vrc_agent/contracts.py` 的字段定义独立写死
一遍，再拿产品代码的真实输出对照：

- 每个场景都必须过得了 `SceneState` 的严格契约（extra=forbid、strict、frozen）；
- 证据必须指向产生它的那一条观测；
- 看不清（窗口非 visible / 没有帧 / 观测过老）必须给 `confidence=None` 的空场景，**且不调用供应商**；
- 模型载荷里的动作/权限字段绝不能变成场景字段，畸形实体只能被丢弃并计数；
- `SceneState` **不是**动作请求：拿它当租约或 epoch 用，控制层必须拒绝；
- 场景与供应商可见面里不得出现帧字节、帧摘要、文件路径和密钥值；
- 失败走 `ProviderError`（受控通道），乱序观测走 `PerceptionError`（编程错误），两者都不产出场景。

与本文件同时记录的**存疑点**（详见交付报告，不改 src）：任务书要求「供应商超时/错误时
`Perception` 产出 unknown scene」，而实现与 `Perception.interpret` 的文档字符串一致地把
`ProviderError` 原样抛出（不产出任何场景）。ARCHITECTURE 第 5 节只要求「设 deadline、超时取消、
丢弃迟到响应」，TASKS.md T05 完成条件只要求「超时/错误/乱序响应不会绕过控制层」——都没有
要求把供应商失败降级成 unknown scene。本文件因此断言**实际行为**（无场景 + 受控异常），
并把这条分歧留在报告里，而不是替实现做决定。
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from vrc_agent.capture.frames import Frame
from vrc_agent.capture.replay import ReplaySource, load_fixture
from vrc_agent.clock import FakeClock
from vrc_agent.config import ControlConfig
from vrc_agent.contracts import (
    IDENTIFIER_PATTERN,
    MAX_IDENTIFIER_LENGTH,
    ActionRequest,
    Observation,
    SceneState,
    new_id,
)
from vrc_agent.control.core import Controller
from vrc_agent.perception import PERCEPTION_FAILURES, Perception, PerceptionError
from vrc_agent.providers.base import (
    CancelToken,
    Deadline,
    ModelInput,
    ModelResponse,
    Usage,
    UsageLedger,
)
from vrc_agent.providers.errors import PROVIDER_FAILURES, ProviderError
from vrc_agent.providers.fake import FakeSceneProvider
from vrc_agent.providers.openai_compat import HttpResponse, OpenAICompatibleProvider
from vrc_agent.transport.osc import RecordingTransport

MS = 1_000_000

#: 回放素材：黑屏/最小化/窗口消失/遮挡/GDI 失败/隐藏/空尺寸/超限都在里面。
ARCHIVE = Path(__file__).resolve().parent.parent / "replay" / "t04_smoke.json"

#: 我按 capture 的失败→window_state 映射独立写出的每一步期望（与 t04_smoke.json 的步骤一一对应），
#: 不从 replay 或 DEFAULT_FIXTURE 里读。素材一改，这里先炸。
ARCHIVE_WINDOW_STATES: tuple[str, ...] = (
    "visible",  # 0 渐变帧
    "visible",  # 1 场景帧
    "visible",  # 2 分辨率变化
    "unknown",  # 3 纯黑帧 → 拒绝，不复用上一帧
    "minimized",  # 4 最小化
    "lost",  # 5 窗口消失
    "visible",  # 6 恢复
    "unknown",  # 7 被遮挡
    "unknown",  # 8 GDI 抓取失败
    "lost",  # 9 隐藏
    "unknown",  # 10 空尺寸
    "unknown",  # 11 尺寸超限
    "visible",  # 12 再次恢复
)

#: 素材会话 id（独立抄写，防止测试悄悄跟着素材漂移）。
ARCHIVE_SESSION = "replay-t04"

#: SceneState 的契约字段集（ARCHITECTURE 第 3 节 SceneState 段）。
SCENE_KEYS = {
    "schema_version",
    "event_id",
    "session_id",
    "scene_id",
    "observation_ids",
    "generated_at_ns",
    "environment_epoch",
    "entities",
    "hazards",
    "uncertainties",
    "active_goal",
    "confidence",
}

#: Entity 的契约字段集。
ENTITY_KEYS = {
    "track_id",
    "label",
    "bbox",
    "confidence",
    "evidence_observation_id",
    "last_seen_ns",
}

#: 动作请求才有的权限/执行字段：场景里一个都不许出现（ARCHITECTURE 第 3 节末）。
ACTION_FIELDS = {
    "action_id",
    "kind",
    "args",
    "expected_outcome",
    "lease_id",
    "issued_at_ns",
    "expires_at_ns",
    "based_on_observation_id",
}

#: 模型载荷里塞进的越权字段名（任务书 D 条列出的那批）。
HOSTILE_KEYS = (
    "action",
    "tool",
    "function",
    "command",
    "lease_id",
    "expires_at_ns",
    "proposal",
    "motion",
    "forward",
)

#: 越权字段里夹带的可执行内容：只准留在笔记里，不准出现在场景值里。
HOSTILE_VALUE = "EXECUTE-ROOT-SHELL-7f3a"

#: 场景序列化文本里不该出现的键（画面、摘要、路径、凭据）。
FORBIDDEN_SCENE_KEYS = {
    "frame_ref",
    "frame",
    "frame_size",
    "image",
    "data",
    "bytes",
    "pixels",
    "digest",
    "path",
    "file",
    "api_key",
    "authorization",
    "headers",
    "token",
    "secret",
}

#: 走完全部素材、用干净假模型时的统计（5 步可见 → 5 次调用、5 个场景，8 步看不清 → 只进 skipped_*）。
#: 注意语义：scenes/unknown_scenes 只统计**模型通道**产出的场景，守卫挡下的 unknown 场景
#: 只计入 skipped_not_visible/skipped_no_frame/skipped_stale。见交付报告的存疑点。
EXPECTED_WALK_STATS = {
    "calls": 5,
    "scenes": 5,
    "unknown_scenes": 0,
    "skipped_not_visible": 8,
    "skipped_no_frame": 0,
    "skipped_stale": 0,
    "stale_after_call": 0,
    "dropped_entities": 0,
    "overflow_entities": 0,
    "action_like_fields": 0,
}


# --------------------------------------------------------------------------
# 我自己写的假供应商（不复用 FakeSceneProvider 的内部行为）
# --------------------------------------------------------------------------


class ScriptedProvider:
    """只回报我指定的载荷，并数调用次数。

    `payload` 可以是常量，也可以是 `ModelInput -> payload` 的函数（证据 id 必须指向本次观测，
    所以多数场景需要按输入现算）。`cite` 非 None 时故意回报错误的 `observation_id`，
    用来复现「供应商串包」。
    """

    name = "contract-scripted"
    paid = False

    def __init__(
        self,
        payload: Any = None,
        *,
        cite: str | None = None,
        model: str = "contract-scripted-v1",
    ) -> None:
        self.payload = payload
        self.cite = cite
        self.model = model
        self.calls = 0
        self.requests: list[ModelInput] = []

    def payload_for(self, model_input: ModelInput) -> Any:
        source = self.payload
        return source(model_input) if callable(source) else source

    def response_for(self, model_input: ModelInput) -> ModelResponse:
        """正常适配器会走校验构造；只有跳过校验的适配器才可能送来非对象载荷。"""
        payload = self.payload_for(model_input)
        fields = {
            "request_id": model_input.request_id,
            "observation_id": self.cite or model_input.observation_id,
            "provider": self.name,
            "model": self.model,
            "usage": Usage(prompt_tokens=None, completion_tokens=None, total_tokens=None),
            "latency_ms": 0,
            "attempts": 1,
            "finish_reason": "scripted",
            "created_at_ns": model_input.created_at_ns,
        }
        if isinstance(payload, dict):
            return ModelResponse(**fields, payload=payload)
        # 唯一能到达 build_scene 的「载荷不是对象」通道：绕过字段校验的适配器。
        return ModelResponse.model_construct(**fields, payload=payload)

    async def infer(
        self,
        model_input: ModelInput,
        *,
        deadline: Deadline = Deadline.unlimited(),
        cancel: CancelToken | None = None,
    ) -> ModelResponse:
        self.calls += 1
        self.requests.append(model_input)
        return self.response_for(model_input)


class GatedProvider(ScriptedProvider):
    """制造「迟到响应」：第一次调用挂起，等第二次调用进门后才返回。"""

    def __init__(self, payload: Any = None) -> None:
        super().__init__(payload)
        self.gate = asyncio.Event()

    async def infer(
        self,
        model_input: ModelInput,
        *,
        deadline: Deadline = Deadline.unlimited(),
        cancel: CancelToken | None = None,
    ) -> ModelResponse:
        self.calls += 1
        self.requests.append(model_input)
        if self.calls == 1:
            await self.gate.wait()
        else:
            self.gate.set()
        return self.response_for(model_input)


class RecordingSink:
    """最小遥测接收器：只留证据，用来检查事件里有没有泄漏密钥。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(self, event: str, **fields: object) -> None:
        self.events.append((event, fields))


# --------------------------------------------------------------------------
# 构造助手
# --------------------------------------------------------------------------


@pytest.fixture
def replay_rig() -> tuple[ReplaySource, FakeClock]:
    """回放源与感知**共用**同一个虚拟时钟：观测年龄恒为 0，测试不依赖真实时间。"""
    fixture = load_fixture(ARCHIVE)
    clock = FakeClock(fixture.start_ns)
    return ReplaySource(fixture, clock=clock), clock


def _observation(
    clock: FakeClock, session_id: str = ARCHIVE_SESSION, **changes: object
) -> Observation:
    values: dict[str, object] = {
        "session_id": session_id,
        "observation_id": new_id(),
        "captured_at_ns": clock.now_ns(),
        "environment_epoch": 1,
        "window_state": "visible",
        "frame_ref": "frame-contract-1",
        "frame_size": (2, 3),
    }
    values.update(changes)
    return Observation(**values)  # type: ignore[arg-type]


def _frame(ref: str = "frame-contract-1") -> Frame:
    """2×3 的极小内存帧：只有 ref 与字节数有意义，供应商由我控制。"""
    return Frame(ref=ref, data=bytes(4 * 2 * 3), width=2, height=3, captured_at_ns=0)


def _interpret(
    perception: Perception, observation: Observation, frame: Frame | None = None
) -> SceneState:
    return asyncio.run(perception.interpret(observation, frame=frame))


def _walk(
    source: ReplaySource, perception: Perception
) -> list[tuple[Observation, Frame | None, SceneState]]:
    records: list[tuple[Observation, Frame | None, SceneState]] = []
    for _ in range(len(source.fixture.steps)):
        observation = source.next_observation()
        frame = source.frame_for(observation)
        records.append((observation, frame, _interpret(perception, observation, frame)))
    return records


def _good_entity(observation_id: str, **changes: object) -> dict[str, object]:
    entity: dict[str, object] = {
        "track_id": "landmark_1",
        "label": "lamp",
        "bbox": [0.25, 0.25, 0.5, 0.75],
        "confidence": 0.7,
        "evidence_observation_id": observation_id,
    }
    entity.update(changes)
    return entity


def _entities_payload(*entities: object, uncertainties: object = ()) -> dict[str, object]:
    return {"entities": list(entities), "hazards": [], "uncertainties": list(uncertainties)}


def _hostile_payload(observation_id: str) -> dict[str, object]:
    """一个「正常实体 + 满身越权字段」的载荷。"""
    entity = _good_entity(
        observation_id,
        action={"kind": "motion", "args": {"forward": 1.0, "duration_ms": 500}},
        lease_id="lease-from-the-model",
    )
    return {
        "entities": [entity],
        "hazards": [],
        "uncertainties": [],
        "active_goal": "look at the lamp",
        "action": {"kind": "motion", "args": {"forward": 1.0, "duration_ms": 500}},
        "tool": "shell",
        "function": "execute",
        "command": HOSTILE_VALUE,
        "lease_id": "lease-from-the-model",
        "expires_at_ns": 9_999_999_999,
        "proposal": {"kind": "motion", "args": {"forward": 1.0}},
        "motion": {"forward": 1.0, "duration_ms": 500},
        "forward": 1.0,
    }


def _bad_entities(observation_id: str) -> list[object]:
    """每一条都违反一条独立规则；全部必须被丢弃。"""
    return [
        _good_entity(observation_id, bbox=[-0.2, 0.1, 0.4, 0.6]),  # 越界
        _good_entity(observation_id, bbox=[0.6, 0.6, 0.2, 0.2]),  # 倒序
        _good_entity(observation_id, bbox=[0.1, 0.1, 0.4]),  # 少于 4 个数
        _good_entity(observation_id, bbox=[0.1, 0.1, 0.4, 0.6, 0.8]),  # 多于 4 个数
        _good_entity(observation_id, bbox=["a", "b", "c", "d"]),  # 不是数字
        _good_entity(observation_id, bbox="0.1,0.1,0.4,0.6"),  # 不是列表
        _good_entity(observation_id, confidence=1.5),  # 超过 1
        _good_entity(observation_id, confidence=-0.1),  # 小于 0
        _good_entity(observation_id, confidence="0.7"),  # 字符串
        _good_entity(observation_id, confidence=True),  # 布尔冒充数字
        _good_entity(observation_id, track_id="track/../../etc"),  # 非法字符
        _good_entity(observation_id, track_id="track id"),  # 空格
        _good_entity(observation_id, track_id="t" * (MAX_IDENTIFIER_LENGTH + 1)),  # 过长
        _good_entity(observation_id, track_id=7),  # 不是字符串
        _good_entity("some-other-observation"),  # 证据不是本观测
        {k: v for k, v in _good_entity(observation_id).items() if k != "evidence_observation_id"},
        {k: v for k, v in _good_entity(observation_id).items() if k != "label"},
        "not an object",
    ]


def _json_keys(value: object) -> set[str]:
    """递归收集 JSON 结构里出现过的所有键名。"""
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys |= _json_keys(item)
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for item in value:
            keys |= _json_keys(item)
        return keys
    return set()


def _scene_identifiers(scene: SceneState) -> list[str]:
    values = [scene.event_id, scene.session_id, scene.scene_id, *scene.observation_ids]
    for entity in scene.entities:
        values.extend([entity.track_id, entity.evidence_observation_id])
    return values


def _armed_controller(clock: FakeClock, session_id: str) -> Controller:
    core = Controller(session_id, ControlConfig(), RecordingTransport(), clock)
    core.arm(True)
    return core


def _request(
    core: Controller, clock: FakeClock, observation_id: str, **changes: object
) -> ActionRequest:
    values: dict[str, object] = {
        "session_id": core.session_id,
        "action_id": new_id(),
        "kind": "motion",
        "args": {"forward": 0.5, "duration_ms": 200},
        "lease_id": core.lease_id or "expired-lease",
        "environment_epoch": core.environment_epoch,
        "based_on_observation_id": observation_id,
        "issued_at_ns": clock.now_ns(),
        "expires_at_ns": clock.now_ns() + 2_000_000_000,
    }
    values.update(changes)
    return ActionRequest.model_validate(values)


# --------------------------------------------------------------------------
# A. 严格契约：每个场景都能过 SceneState 的严格校验
# --------------------------------------------------------------------------


def test_every_scene_passes_the_strict_contract_and_has_exactly_the_documented_fields(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    assert source.fixture.session_id == ARCHIVE_SESSION
    perception = Perception(FakeSceneProvider(), clock=clock)
    records = _walk(source, perception)

    assert [observation.window_state for observation, _, _ in records] == list(
        ARCHIVE_WINDOW_STATES
    )
    assert len(records) == 13

    for observation, _, scene in records:
        assert isinstance(scene, SceneState)
        # 严格模式 + extra=forbid 的再校验：字段类型、范围、tuple 化全都过一遍。
        assert SceneState.model_validate(scene.model_dump()) == scene
        # IPC 入口（JSON）也必须能还原同一个场景。
        assert SceneState.model_validate_json(scene.model_dump_json()) == scene
        assert set(json.loads(scene.model_dump_json())) == SCENE_KEYS
        assert scene.schema_version == 1
        assert scene.session_id == observation.session_id
        assert scene.environment_epoch == observation.environment_epoch
        assert scene.observation_ids == (observation.observation_id,)
        assert isinstance(scene.entities, tuple)
        assert isinstance(scene.hazards, tuple)
        assert isinstance(scene.uncertainties, tuple)
        assert scene.confidence is None or 0.0 <= scene.confidence <= 1.0


def test_scenes_are_frozen_and_reject_extra_fields(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    perception = Perception(FakeSceneProvider(), clock=clock)
    observation = source.next_observation()
    scene = _interpret(perception, observation, source.frame_for(observation))

    with pytest.raises(ValidationError):
        scene.confidence = 0.5  # type: ignore[misc]
    with pytest.raises(ValidationError):
        SceneState.model_validate({**scene.model_dump(), "unexpected": 1})


def test_walk_statistics_match_the_independently_derived_step_expectations(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    perception = Perception(FakeSceneProvider(), clock=clock)
    _walk(source, perception)
    # 13 步里只有 5 步可见；其余 8 步必须在**调用供应商之前**就被挡下。
    assert perception.stats() == EXPECTED_WALK_STATS


# --------------------------------------------------------------------------
# B. 证据约束与置信度语义
# --------------------------------------------------------------------------


def test_every_entity_cites_the_observation_that_produced_the_scene(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    perception = Perception(FakeSceneProvider(), clock=clock)
    records = _walk(source, perception)

    total = 0
    for observation, _, scene in records:
        assert len(scene.observation_ids) == 1
        assert scene.observation_ids[0] == observation.observation_id
        for entity in scene.entities:
            total += 1
            # 证据必须是产生这个场景的那一条观测，不能是别的观测或凭空的 id。
            assert entity.evidence_observation_id == scene.observation_ids[0]
            assert entity.evidence_observation_id == observation.observation_id
            assert set(json.loads(entity.model_dump_json())) == ENTITY_KEYS
            x1, y1, x2, y2 = entity.bbox
            assert 0.0 <= x1 < x2 <= 1.0 and 0.0 <= y1 < y2 <= 1.0
            assert entity.label.strip()
            assert 0.0 <= entity.confidence <= 1.0
    # 5 个可见步骤各出一条实体——否则上面全是空转。
    assert total == 5


def test_scene_confidence_is_the_minimum_entity_confidence_or_none(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    perception = Perception(FakeSceneProvider(), clock=clock)
    for _, _, scene in _walk(source, perception):
        if scene.entities:
            assert scene.confidence == min(entity.confidence for entity in scene.entities)
        else:
            assert scene.confidence is None
            assert scene.uncertainties


def test_the_model_input_carries_the_frame_in_memory_and_cites_the_observation(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(
        payload=lambda model_input: _entities_payload(_good_entity(model_input.observation_id))
    )
    perception = Perception(provider, clock=clock)
    observation = source.next_observation()
    frame = source.frame_for(observation)
    assert frame is not None

    scene = _interpret(perception, observation, frame)
    assert provider.calls == 1
    request = provider.requests[0]
    assert request.observation_id == observation.observation_id == scene.observation_ids[0]
    # 画面以**内存字节**交给供应商，不是路径；引用仍是 capture 的受限 id。
    assert request.image == frame.data
    assert request.image_ref == frame.ref
    assert request.image_size == frame.size
    assert request.session_id == observation.session_id
    assert request.environment_epoch == observation.environment_epoch
    assert request.prompt.strip()


# --------------------------------------------------------------------------
# C. 看不清就 unknown，而且不许打扰供应商
# --------------------------------------------------------------------------


def test_invisible_or_broken_capture_never_reaches_the_provider(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(payload=_entities_payload(_good_entity("ignored")))
    perception = Perception(provider, clock=clock)

    blocked = 0
    for expected in ARCHIVE_WINDOW_STATES:
        observation = source.next_observation()
        frame = source.frame_for(observation)
        before = provider.calls
        scene = _interpret(perception, observation, frame)
        if expected == "visible":
            assert provider.calls == before + 1
            continue
        blocked += 1
        assert provider.calls == before, f"{expected} 这一步不该调用供应商"
        assert scene.confidence is None, expected
        assert scene.entities == (), expected
        assert scene.uncertainties, expected
    assert blocked == 8
    assert provider.calls == 5, "全部素材里只有 5 个可见步骤该到供应商"
    assert perception.stats()["calls"] == 5, "只有可见步骤该计入调用"
    assert perception.stats()["skipped_not_visible"] == 8


def test_an_observation_without_frame_bytes_is_unknown_and_calls_nothing(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(payload=_entities_payload(_good_entity("ignored")))
    perception = Perception(provider, clock=clock)
    observation = source.next_observation()
    assert observation.window_state == "visible"

    scene = _interpret(perception, observation, frame=None)
    assert scene.confidence is None
    assert scene.entities == ()
    assert scene.uncertainties
    assert provider.calls == 0
    assert perception.stats()["skipped_no_frame"] == 1


def test_an_observation_older_than_the_max_age_is_unknown_and_calls_nothing(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(
        payload=lambda model_input: _entities_payload(_good_entity(model_input.observation_id))
    )
    perception = Perception(provider, clock=clock)
    observation = source.next_observation()
    frame = source.frame_for(observation)

    clock.advance_ms(1_501)  # 默认上限 1500 ms
    scene = _interpret(perception, observation, frame)
    assert scene.confidence is None
    assert scene.entities == ()
    assert scene.uncertainties
    assert provider.calls == 0
    assert perception.stats()["skipped_stale"] == 1

    # 文档规定 0 表示不做年龄检查：同一份过老观测必须重新进入供应商通道。
    permissive = Perception(provider, clock=clock, max_observation_age_ms=0)
    fresh = _interpret(permissive, observation, frame)
    assert provider.calls == 1
    assert fresh.confidence is not None
    assert permissive.stats()["skipped_stale"] == 0


# --------------------------------------------------------------------------
# D. 模型的越权字段不能变成场景字段
# --------------------------------------------------------------------------


def test_action_like_model_fields_never_become_scene_fields(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(payload=_hostile_payload("placeholder"))
    perception = Perception(provider, clock=clock)
    # 证据必须指向本次观测，所以按输入重算载荷。
    provider.payload = lambda model_input: _hostile_payload(model_input.observation_id)
    observation = source.next_observation()
    scene = _interpret(perception, observation, source.frame_for(observation))

    text = scene.model_dump_json()
    document = json.loads(text)
    # 描述性的东西留下：模型确实可以描述它看到了什么。
    assert scene.active_goal == "look at the lamp"
    assert len(scene.entities) == 1
    assert set(json.loads(scene.entities[0].model_dump_json())) == ENTITY_KEYS

    # 可执行/权限字段一个都不许进来：键集、JSON 文本、值都查一遍。
    assert not (_json_keys(document) & set(HOSTILE_KEYS))
    assert not (set(document) & ACTION_FIELDS)
    for name in HOSTILE_KEYS:
        assert f'"{name}"' not in text, name
    assert HOSTILE_VALUE not in text
    assert "shell" not in text

    # 越权载荷要被留痕：统计里计数，uncertainties 里说明。
    assert perception.stats()["action_like_fields"] > 0
    assert scene.uncertainties


# --------------------------------------------------------------------------
# E. 畸形输出只许被丢弃，不许炸到调用方
# --------------------------------------------------------------------------


def test_malformed_entities_are_dropped_and_counted(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    bad_count = len(_bad_entities("placeholder"))

    def payload(model_input: ModelInput) -> dict[str, object]:
        return _entities_payload(
            *_bad_entities(model_input.observation_id),
            _good_entity(model_input.observation_id, track_id="ok_1", confidence=0.7),
            _good_entity(model_input.observation_id, track_id="ok_2", confidence=0.3),
        )

    provider = ScriptedProvider(payload=payload)
    perception = Perception(provider, clock=clock)
    observation = source.next_observation()
    scene = _interpret(perception, observation, source.frame_for(observation))

    assert [entity.track_id for entity in scene.entities] == ["ok_1", "ok_2"]
    assert scene.confidence == 0.3
    assert scene.uncertainties
    assert perception.stats()["dropped_entities"] == bad_count
    assert perception.stats()["scenes"] == 1


NON_OBJECT_PAYLOADS: tuple[object, ...] = (["not", "an", "object"], "just a string", 42, None)
#: (载荷, 期望场景里是否留下原因文本)。五种情况都必须在场景里留下解释：
#: 空实体列表原本不写原因，T05 收尾时按本文件交付报告的存疑点 3 改了实现（见验证记录），
#: 现在由 scene.py 在「没有可用实体且模型没给解释」时补一句默认原因。
ENTITYLESS_PAYLOADS: tuple[tuple[object, bool], ...] = (
    ({}, True),
    ({"uncertainties": ["nothing visible"]}, True),
    ({"entities": "many"}, True),
    ({"entities": None}, True),
    ({"entities": []}, True),
)


@pytest.mark.parametrize("payload", NON_OBJECT_PAYLOADS, ids=lambda value: repr(value)[:24])
def test_a_payload_that_is_not_an_object_is_unknown_not_a_crash(
    replay_rig: tuple[ReplaySource, FakeClock], payload: object
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(payload=payload)
    perception = Perception(provider, clock=clock)
    observation = source.next_observation()

    scene = _interpret(perception, observation, source.frame_for(observation))
    assert provider.calls == 1
    assert scene.confidence is None
    assert scene.entities == ()
    assert scene.uncertainties
    assert scene.observation_ids == (observation.observation_id,)
    assert perception.stats()["unknown_scenes"] == 1
    assert perception.stats()["dropped_entities"] == 0


@pytest.mark.parametrize(
    ("payload", "has_reason"),
    ENTITYLESS_PAYLOADS,
    ids=lambda value: repr(value)[:24],
)
def test_a_payload_without_usable_entities_is_an_unknown_scene(
    replay_rig: tuple[ReplaySource, FakeClock], payload: object, has_reason: bool
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(payload=payload)
    perception = Perception(provider, clock=clock)
    observation = source.next_observation()

    scene = _interpret(perception, observation, source.frame_for(observation))
    assert provider.calls == 1
    assert scene.confidence is None
    assert scene.entities == ()
    assert bool(scene.uncertainties) is has_reason
    assert perception.stats()["scenes"] == 1
    assert perception.stats()["unknown_scenes"] == 1


def test_an_entity_missing_a_required_field_is_dropped_and_counted(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(payload=_entities_payload({"track_id": "partial"}))
    perception = Perception(provider, clock=clock)
    observation = source.next_observation()

    scene = _interpret(perception, observation, source.frame_for(observation))
    assert scene.confidence is None
    assert scene.entities == ()
    assert perception.stats()["dropped_entities"] == 1
    assert perception.stats()["calls"] == 1


# --------------------------------------------------------------------------
# F. 控制层不被绕过：SceneState 不是动作请求
# --------------------------------------------------------------------------


def test_a_scene_cannot_be_used_as_an_action_request(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    core = _armed_controller(clock, source.fixture.session_id)
    observation = source.next_observation()
    assert core.observe(observation) is True
    scene = _interpret(
        Perception(FakeSceneProvider(), clock=clock), observation, source.frame_for(observation)
    )
    assert scene.confidence is not None

    document = json.loads(scene.model_dump_json())
    assert not (set(document) & ACTION_FIELDS), "场景不得携带权限/执行字段"
    assert set(document) == SCENE_KEYS

    # 拿 scene_id 当租约：场景里根本没有租约，控制层只认它自己签发的那一个。
    stolen = core.submit(_request(core, clock, scene.observation_ids[0], lease_id=scene.scene_id))
    assert stolen.status == "REJECTED"
    assert stolen.reason_code == "EXPIRED_LEASE"

    # 场景自带的 epoch 只是观测的 epoch，不是权限；动了它就必须被拒。
    wrong_epoch = core.submit(
        _request(
            core, clock, scene.observation_ids[0], environment_epoch=scene.environment_epoch + 1
        )
    )
    assert wrong_epoch.status == "REJECTED"
    assert wrong_epoch.reason_code == "EPOCH_MISMATCH"

    # 对照组：同一个观测 + 真实租约 + 正确 epoch 就能过——前两次拒绝确实是权限字段造成的。
    accepted = core.submit(_request(core, clock, scene.observation_ids[0]))
    assert accepted.status == "RUNNING"
    assert accepted.reason_code == "OK"
    assert accepted.verification == "UNVERIFIED"


def test_a_request_still_needs_a_known_visible_fresh_observation(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    session = source.fixture.session_id
    core = _armed_controller(clock, session)

    # 1) 控制层从未见过的观测（场景里凭空写一个 id 也一样）。
    unseen = core.submit(_request(core, clock, "never-seen-observation"))
    assert unseen.status == "REJECTED"
    assert unseen.reason_code == "STALE_OBSERVATION"

    # 2) 见过、visible，但已经过了最大年龄：lease 还有效，照样拒绝。
    observation = source.next_observation()
    assert observation.window_state == "visible"
    assert core.observe(observation) is True
    clock.advance_ms(2_000)
    core.heartbeat()  # 只刷新心跳，不刷新观测年龄
    assert core.state == "ARMED_IDLE"
    aged_out = core.submit(_request(core, clock, observation.observation_id))
    assert aged_out.status == "REJECTED"
    assert aged_out.reason_code == "STALE_OBSERVATION"

    # 3) 非 visible 的观测在进入控制层那一刻就把控制器停住：动作通道连租约都没了。
    paused = _armed_controller(clock, session)
    degraded = _observation(
        clock,
        session_id=session,
        window_state="minimized",
        frame_ref=None,
        frame_size=None,
    )
    assert paused.observe(degraded) is True
    assert paused.state == "PAUSED"
    assert paused.reason == "FOCUS_LOST"
    assert paused.lease_id is None
    blocked = paused.submit(_request(paused, clock, degraded.observation_id))
    assert blocked.status == "REJECTED"
    assert blocked.reason_code == "NOT_ARMED"


# --------------------------------------------------------------------------
# G. 隐私：没有像素、摘要、路径和密钥
# --------------------------------------------------------------------------


def test_scene_serialization_carries_no_pixels_digests_or_paths(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    perception = Perception(FakeSceneProvider(), clock=clock)
    frames_checked = 0

    for observation, frame, scene in _walk(source, perception):
        text = scene.model_dump_json()
        assert not (_json_keys(json.loads(text)) & FORBIDDEN_SCENE_KEYS)
        for identifier in _scene_identifiers(scene):
            # 标识符受 contracts.Identifier 约束：不可能带路径分隔符或盘符。
            assert re.fullmatch(IDENTIFIER_PATTERN, identifier), identifier
            assert "/" not in identifier and "\\" not in identifier and ":" not in identifier
            assert Path(identifier).name == identifier
        if frame is None:
            continue
        frames_checked += 1
        assert frame.digest() not in text, "帧 sha256 不得出现在场景里"
        assert frame.data.decode("latin-1") not in text, "帧字节不得出现在场景里"
        assert len(text) < len(frame.data), "场景应该远小于一帧原始画面"

    assert frames_checked == 5
    assert observation.session_id == ARCHIVE_SESSION


def test_provider_surfaces_never_echo_the_api_key() -> None:
    secret = "sk-contract-9f3a-do-not-leak"
    clock = FakeClock()
    ledger = UsageLedger(budget_usd=1.0)
    sink = RecordingSink()
    sent: list[Any] = []

    def transport(request: Any) -> HttpResponse:
        sent.append(request)
        body = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": json.dumps({"entities": []})},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return HttpResponse(status=200, body=json.dumps(body).encode("utf-8"))

    provider = OpenAICompatibleProvider(
        model="contract-model",
        api_key=secret,
        base_url="http://127.0.0.1:9000/v1",
        max_retries=0,
        transport=transport,
        clock=clock,
        ledger=ledger,
        sink=sink,
    )
    model_input = ModelInput(
        prompt="describe the frame",
        observation_id="obs-secret-1",
        request_id="req-secret-1",
        session_id=ARCHIVE_SESSION,
        environment_epoch=1,
    )
    response = asyncio.run(
        provider.infer(
            model_input,
            deadline=Deadline.from_now(clock, 5_000),
            cancel=CancelToken(),
        )
    )

    # 正向：密钥确实被用来发请求（否则下面的「不泄漏」全部是空转）。
    assert sent[0].headers["Authorization"] == f"Bearer {secret}"
    assert secret.encode("utf-8") not in sent[0].body
    # 反向：任何可被打印/上报的表面都不许出现它。
    assert secret not in repr(provider)
    assert secret not in repr(response)
    assert secret not in response.model_dump_json()
    assert secret not in json.dumps(ledger.snapshot())
    assert secret not in json.dumps(sink.events)
    assert secret not in repr(FakeSceneProvider())

    assert ledger.calls == 1
    snapshot = ledger.snapshot()
    assert snapshot["cost_complete"] is False, "缺价格表时费用未知，不能报成零费用"
    assert snapshot["unknown_token_calls"] == 0


# --------------------------------------------------------------------------
# H. 失败通道：受控异常，绝不产出可疑场景
# --------------------------------------------------------------------------


def test_provider_failure_codes_are_a_closed_set() -> None:
    for code in PROVIDER_FAILURES:
        assert ProviderError(code).failure == code
    assert ProviderError("TIMEOUT").retryable is False
    assert ProviderError("TRANSPORT_ERROR").retryable is True
    with pytest.raises(ValueError):
        ProviderError("NOT_A_REAL_FAILURE")  # type: ignore[arg-type]
    assert {"TIMEOUT", "CANCELLED", "TRANSPORT_ERROR", "REFUSED"} <= set(PROVIDER_FAILURES)


@pytest.mark.parametrize(
    ("behaviour", "failure"),
    [
        ("timeout", "TIMEOUT"),
        ("cancelled", "CANCELLED"),
        ("error", "TRANSPORT_ERROR"),
        ("refused", "REFUSED"),
    ],
)
def test_fake_provider_behaviours_raise_documented_failures(
    behaviour: str, failure: str, replay_rig: tuple[ReplaySource, FakeClock]
) -> None:
    provider = FakeSceneProvider(behaviour=behaviour)  # type: ignore[arg-type]
    model_input = ModelInput(prompt="p", observation_id="obs-1", request_id="req-1")

    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            provider.infer(model_input, deadline=Deadline.unlimited(), cancel=CancelToken())
        )
    assert caught.value.failure == failure
    assert caught.value.failure in PROVIDER_FAILURES

    # 同一条失败经过 Perception 只有一种去向：抛出受控异常，不产出任何场景。
    source, clock = replay_rig
    ledger = UsageLedger()
    perception = Perception(
        FakeSceneProvider(behaviour=behaviour, ledger=ledger),  # type: ignore[arg-type]
        clock=clock,
    )
    observation = source.next_observation()
    with pytest.raises(ProviderError) as caught:
        _interpret(perception, observation, source.frame_for(observation))
    assert caught.value.failure == failure
    assert perception.stats()["calls"] == 1
    assert perception.stats()["scenes"] == 0
    assert perception.stats()["unknown_scenes"] == 0
    # 免费假模型失败的账目：计一次调用与未知 token，但费用确实是 0（不是未知）。
    assert ledger.calls == 1
    assert ledger.snapshot()["cost_complete"] is True


def test_a_provider_failure_yields_no_scene_at_all(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    """存疑点落点：供应商失败时不降级成 unknown scene，而是抛出受控异常。

    ARCHITECTURE 第 5 节只要求「设 deadline、超时取消、丢弃迟到响应」，TASKS.md T05 完成条件
    只要求「超时/错误/乱序响应不会绕过控制层」。没有任何场景产出 = 下游拿不到可执行依据，
    这比给一个可疑场景更保守；任务书里「产出 unknown scene」的期望与实现不符，记在报告里。
    """
    source, clock = replay_rig
    perception = Perception(FakeSceneProvider(behaviour="timeout"), clock=clock)
    observation = source.next_observation()

    with pytest.raises(ProviderError) as caught:
        _interpret(perception, observation, source.frame_for(observation))
    assert caught.value.failure in PROVIDER_FAILURES
    assert perception.stats()["scenes"] == 0
    assert perception.stats()["unknown_scenes"] == 0

    # 同一时刻喂一份看不清的观测仍然走 unknown 场景（两条失败通道互不混淆）。
    # 守卫挡下的场景只计入 skipped_*，不计入 scenes/unknown_scenes（见 EXPECTED_WALK_STATS 注释）。
    degraded = _observation(clock, window_state="unknown")
    scene = _interpret(perception, degraded, None)
    assert scene.confidence is None
    assert scene.entities == ()
    assert scene.uncertainties
    assert perception.stats()["unknown_scenes"] == 0
    assert perception.stats()["skipped_not_visible"] == 1


def test_an_older_observation_is_rejected_instead_of_interpreted(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(
        payload=lambda model_input: _entities_payload(_good_entity(model_input.observation_id))
    )
    perception = Perception(provider, clock=clock)
    frame = _frame()
    now = clock.now_ns()

    newer = _observation(clock, observation_id="obs-newer", captured_at_ns=now - 100 * MS)
    older = _observation(clock, observation_id="obs-older", captured_at_ns=now - 900 * MS)

    assert _interpret(perception, newer, frame).confidence is not None
    with pytest.raises(PerceptionError) as caught:
        _interpret(perception, older, frame)
    assert caught.value.failure == "OUT_OF_ORDER"
    assert caught.value.failure in PERCEPTION_FAILURES
    assert provider.calls == 1, "乱序观测不该到供应商"
    assert perception.stats()["scenes"] == 1


def test_a_response_for_another_observation_is_rejected_not_trusted(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    source, clock = replay_rig
    provider = ScriptedProvider(
        payload=lambda model_input: _entities_payload(_good_entity(model_input.observation_id)),
        cite="some-other-observation",
    )
    perception = Perception(provider, clock=clock)
    observation = source.next_observation()

    with pytest.raises(PerceptionError) as caught:
        _interpret(perception, observation, source.frame_for(observation))
    assert caught.value.failure == "MISROUTED_RESPONSE"
    assert caught.value.failure in PERCEPTION_FAILURES
    assert provider.calls == 1
    assert perception.stats()["scenes"] == 0
    with pytest.raises(ValueError):
        PerceptionError("NOT_A_REAL_FAILURE")  # type: ignore[arg-type]


def test_a_late_response_cannot_overwrite_a_newer_scene(
    replay_rig: tuple[ReplaySource, FakeClock],
) -> None:
    """迟到的响应必须被丢弃：它属于更旧的观测，不能覆盖更新的场景。"""
    source, clock = replay_rig
    provider = GatedProvider(
        payload=lambda model_input: _entities_payload(_good_entity(model_input.observation_id))
    )
    perception = Perception(provider, clock=clock)
    frame = _frame()
    now = clock.now_ns()
    older = _observation(clock, observation_id="obs-older", captured_at_ns=now - 1_000 * MS)
    newer = _observation(clock, observation_id="obs-newer", captured_at_ns=now - 500 * MS)

    async def both() -> list[object]:
        return list(
            await asyncio.gather(
                perception.interpret(older, frame=frame),
                perception.interpret(newer, frame=frame),
                return_exceptions=True,
            )
        )

    # 等待只是防止测试挂死的看门狗；两条请求的先后由事件门保证，不依赖真实时间。
    results = asyncio.run(asyncio.wait_for(both(), timeout=5))

    assert isinstance(results[0], PerceptionError)
    assert results[0].failure == "OUT_OF_ORDER"
    assert isinstance(results[1], SceneState)
    assert results[1].observation_ids == (newer.observation_id,)
    assert results[1].confidence is not None
    assert perception.stats()["calls"] == 2
    assert perception.stats()["scenes"] == 1
