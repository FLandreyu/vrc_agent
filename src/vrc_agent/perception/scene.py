"""把不可信的模型载荷变成受约束的 `SceneState`。

这里是「模型说什么」与「系统承认什么」之间的唯一闸门，规则刻意保守：

- 模型载荷是**不可信输入**：字段类型不对、bbox 越界、证据对不上，一律**丢弃该实体**
  并把原因写进 `uncertainties`，绝不修补、绝不猜测。
- 看不清就是 `confidence=None` + 空 `entities`（契约本身也禁止「不知道却声称看到了东西」）。
- 模型载荷里出现 `action`/`kind`/`args`/`lease_id` 之类的**动作或权限字段**时全部忽略并留痕：
  模型只能描述看到了什么，不能提出可执行动作，更不能生成租约（ARCHITECTURE 第 3 节）。
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from vrc_agent.contracts import (
    IDENTIFIER_PATTERN,
    Entity,
    Observation,
    SceneState,
)

#: 描述性文本的清洗上限，防止供应商用巨量文本撑爆日志或内存。
MAX_TEXT_CHARS = 200
MAX_UNCERTAINTIES = 16
MAX_HAZARDS = 16
MAX_ACTIVE_GOAL_CHARS = 200
ENTITY_JSON_LIMIT = 256

_IDENTIFIER_RE = re.compile(IDENTIFIER_PATTERN)

#: 出现即视为「模型想越权」的字段名（大小写不敏感）。
ACTION_LIKE_KEYS: frozenset[str] = frozenset(
    {
        "action",
        "actions",
        "action_proposal",
        "args",
        "capability",
        "capabilities",
        "command",
        "duration_ms",
        "expires_at_ns",
        "forward",
        "issued_at_ns",
        "kind",
        "lease_id",
        "osc",
        "osculator",
        "path",
        "permissions",
        "role",
        "shell",
        "skill",
        "strafe",
        "tool",
        "url",
        "yaw",
    }
)


@dataclass(frozen=True, slots=True)
class SceneDraft:
    """构建结果：场景 + 丢弃/越权的计数，供统计、遥测和测试断言。"""

    scene: SceneState
    dropped_entities: int = 0
    overflow_entities: int = 0
    action_like_fields: tuple[str, ...] = ()
    unknown_reason: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def _clean_text(value: object, *, limit: int = MAX_TEXT_CHARS) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not text:
        return None
    return text[:limit]


def _clean_texts(value: object, *, limit: int, max_items: int) -> tuple[tuple[str, ...], int]:
    """返回 (清洗后的字符串, 被丢弃的条数)。非列表输入整体丢弃。"""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return (), 0 if value is None else 1
    kept: list[str] = []
    dropped = 0
    for item in value:
        text = _clean_text(item)
        if text is None:
            dropped += 1
            continue
        kept.append(text)
    overflow = max(0, len(kept) - max_items)
    return tuple(kept[:max_items]), dropped + overflow


def _bbox(value: object) -> tuple[float, float, float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if len(value) != 4:
        return None
    numbers: list[float] = []
    for raw in value:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return None
        number = float(raw)
        if not math.isfinite(number):
            return None
        numbers.append(number)
    return numbers[0], numbers[1], numbers[2], numbers[3]


def _confidence(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        return None
    return number


def _identifier(value: object, *, limit: int = 64) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not 1 <= len(text) <= limit or not _IDENTIFIER_RE.fullmatch(text):
        return None
    return text


def _track_id(value: object) -> str | None:
    return _identifier(value, limit=128)


def _nano_time(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _find_action_like_keys(payload: Mapping[str, Any], *, depth: int = 0) -> tuple[str, ...]:
    """递归找出动作/权限字段名（最多看两层，够用且不会被深层结构拖住）。"""
    found: list[str] = []
    if depth > 2:
        return ()
    for key, value in payload.items():
        if str(key).lower() in ACTION_LIKE_KEYS:
            found.append(str(key))
        elif isinstance(value, Mapping):
            found.extend(_find_action_like_keys(value, depth=depth + 1))
    return tuple(dict.fromkeys(found))


def _parse_entity(
    item: object,
    observation: Observation,
    *,
    index: int,
) -> tuple[Entity | None, str | None]:
    """解析单个实体；任何不合格之处都返回原因字符串，由调用方计入 uncertainties。"""
    if not isinstance(item, Mapping):
        return None, f"entity[{index}] is not an object"
    track_id = _track_id(item.get("track_id"))
    if track_id is None:
        return None, f"entity[{index}] has no usable track_id"
    label = _clean_text(item.get("label"), limit=64)
    if label is None:
        return None, f"entity[{index}] ({track_id}) has no usable label"
    bbox = _bbox(item.get("bbox"))
    if bbox is None:
        return None, f"entity[{index}] ({track_id}) has a malformed bbox"
    confidence = _confidence(item.get("confidence"))
    if confidence is None:
        return None, f"entity[{index}] ({track_id}) has no usable confidence"
    evidence = _identifier(item.get("evidence_observation_id"))
    if evidence != observation.observation_id:
        return None, f"entity[{index}] ({track_id}) cites evidence that is not this observation"
    last_seen = _nano_time(item.get("last_seen_ns"))
    if last_seen is None:
        last_seen = observation.captured_at_ns
    try:
        entity = Entity(
            track_id=track_id,
            label=label,
            bbox=bbox,
            confidence=confidence,
            evidence_observation_id=evidence,
            last_seen_ns=last_seen,
        )
    except ValidationError as exc:
        reason = exc.errors()[0].get("msg", "invalid entity") if exc.errors() else "invalid entity"
        return None, f"entity[{index}] ({track_id}) rejected: {reason}"
    return entity, None


def unknown_scene(
    observation: Observation,
    *,
    reason: str,
    generated_at_ns: int,
) -> SceneState:
    """「没有可用场景」：零实体、`confidence=None`、原因写在 uncertainties 里。"""
    text = _clean_text(reason) or "unknown"
    return SceneState(
        session_id=observation.session_id,
        environment_epoch=observation.environment_epoch,
        observation_ids=(observation.observation_id,),
        generated_at_ns=generated_at_ns,
        entities=(),
        hazards=(),
        uncertainties=(text,),
        active_goal=None,
        confidence=None,
    )


def build_scene(
    payload: object,
    observation: Observation,
    *,
    generated_at_ns: int,
    max_entities: int = 64,
) -> SceneDraft:
    """把模型载荷转成场景；不可信的实体被丢弃并留痕。"""
    if max_entities < 1:
        raise ValueError("max_entities must be >= 1")
    if not isinstance(payload, Mapping):
        return SceneDraft(
            scene=unknown_scene(
                observation,
                reason="model payload is not an object; nothing is trusted",
                generated_at_ns=generated_at_ns,
            ),
            unknown_reason="payload_not_object",
        )

    notes: list[str] = []
    uncertainties, uncertainty_drops = _clean_texts(
        payload.get("uncertainties"), limit=MAX_TEXT_CHARS, max_items=MAX_UNCERTAINTIES
    )
    if uncertainty_drops:
        notes.append(f"uncertainties: dropped {uncertainty_drops} unusable item(s)")

    action_like = _find_action_like_keys(payload)
    if action_like:
        notes.append(
            "ignored action-like field(s) from model payload: " + ", ".join(action_like[:8])
        )

    entities: list[Entity] = []
    dropped = 0
    overflow = 0
    raw_entities = payload.get("entities")
    if raw_entities is None:
        notes.append("payload has no entities field")
    elif not isinstance(raw_entities, Sequence) or isinstance(
        raw_entities, (str, bytes, bytearray)
    ):
        notes.append("entities is not a list; no entity is trusted")
    else:
        for index, item in enumerate(raw_entities):
            if len(entities) >= max_entities:
                overflow += 1
                continue
            entity, problem = _parse_entity(item, observation, index=index)
            if entity is None:
                dropped += 1
                if problem:
                    notes.append(problem)
                continue
            entities.append(entity)

    hazards, hazard_drops = _clean_texts(
        payload.get("hazards"), limit=MAX_TEXT_CHARS, max_items=MAX_HAZARDS
    )
    if hazard_drops:
        notes.append(f"hazards: dropped {hazard_drops} unusable item(s)")

    if dropped:
        notes.append(f"dropped {dropped} unverifiable entity/entities")
    if overflow:
        notes.append(f"dropped {overflow} entity/entities beyond the {max_entities} limit")

    active_goal = payload.get("active_goal")
    goal_text = _clean_text(active_goal, limit=MAX_ACTIVE_GOAL_CHARS) if active_goal else None

    combined = tuple((*uncertainties, *notes))[:MAX_UNCERTAINTIES]
    if not entities:
        # 看不清/没证据：不许强行构造。契约要求此时 confidence 必须为 None。
        reason = "model returned no usable entity; scene is unknown"
        # 即使模型什么都没解释，也必须在场景里留下原因：下游要能回答「为什么不知道」。
        explanation = combined or (reason,)
        return SceneDraft(
            scene=SceneState(
                session_id=observation.session_id,
                environment_epoch=observation.environment_epoch,
                observation_ids=(observation.observation_id,),
                generated_at_ns=generated_at_ns,
                entities=(),
                hazards=hazards,
                uncertainties=explanation,
                active_goal=None,
                confidence=None,
            ),
            dropped_entities=dropped,
            overflow_entities=overflow,
            action_like_fields=action_like,
            unknown_reason="no_usable_entity",
            notes=(*notes, reason),
        )

    #: 场景可信度取实体可信度的**最小值**：整体可信度不该高于最弱的一条声明。
    confidence = min(entity.confidence for entity in entities)
    return SceneDraft(
        scene=SceneState(
            session_id=observation.session_id,
            environment_epoch=observation.environment_epoch,
            observation_ids=(observation.observation_id,),
            generated_at_ns=generated_at_ns,
            entities=tuple(entities),
            hazards=hazards,
            uncertainties=combined,
            active_goal=goal_text,
            confidence=confidence,
        ),
        dropped_entities=dropped,
        overflow_entities=overflow,
        action_like_fields=action_like,
        unknown_reason=None,
        notes=tuple(notes),
    )


__all__ = [
    "ACTION_LIKE_KEYS",
    "MAX_HAZARDS",
    "MAX_TEXT_CHARS",
    "MAX_UNCERTAINTIES",
    "SceneDraft",
    "build_scene",
    "unknown_scene",
]
