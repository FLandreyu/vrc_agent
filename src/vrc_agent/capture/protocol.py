"""模块边界协议。ARCHITECTURE 第 4 节：ObservationSource 只暴露 next_observation()。"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vrc_agent.contracts import Observation


@runtime_checkable
class ObservationSource(Protocol):
    """live 与 replay 输出完全相同的类型，并且带真实采集时刻。"""

    def next_observation(self) -> Observation: ...


@runtime_checkable
class EventSink(Protocol):
    """telemetry.EventLog 的结构子集，避免 capture 依赖具体实现。"""

    def emit(self, event: str, **fields: object) -> None: ...
