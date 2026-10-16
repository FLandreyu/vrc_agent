"""感知层：观测 → 受约束的 `SceneState`。

- `perception/interpreter.py`：`Perception.interpret()`，唯一入口（截止期、取消、乱序防护）。
- `perception/scene.py`：不可信模型载荷 → `SceneState` 的唯一闸门（丢弃、留痕、未知）。
- `perception/errors.py`：感知层的失败原因（与动作拒绝原因、供应商失败分开）。
"""

from __future__ import annotations

from vrc_agent.perception.errors import (
    PERCEPTION_FAILURES,
    PerceptionError,
    PerceptionFailure,
)
from vrc_agent.perception.interpreter import PERCEPTION_PROMPT, Perception
from vrc_agent.perception.scene import (
    ACTION_LIKE_KEYS,
    MAX_HAZARDS,
    MAX_UNCERTAINTIES,
    SceneDraft,
    build_scene,
    unknown_scene,
)

__all__ = [
    "ACTION_LIKE_KEYS",
    "MAX_HAZARDS",
    "MAX_UNCERTAINTIES",
    "PERCEPTION_FAILURES",
    "PERCEPTION_PROMPT",
    "Perception",
    "PerceptionError",
    "PerceptionFailure",
    "SceneDraft",
    "build_scene",
    "unknown_scene",
]
