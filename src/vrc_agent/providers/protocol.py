"""模块边界协议。ARCHITECTURE 第 4 节：ModelProvider 的职责是「供应商调用、结构化
输出、usage、超时」，**不执行动作**。

`infer` 是异步的：模型调用必须可取消或隔离，不能阻塞控制进程。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vrc_agent.providers.base import CancelToken, Deadline, ModelInput, ModelResponse


@runtime_checkable
class ModelProvider(Protocol):
    """所有适配器（假模型和真实供应商）统一暴露的接口。"""

    #: 供遥测与报告使用的稳定标识。
    name: str
    #: True 表示调用可能产生费用，必须受预算台账约束。
    paid: bool

    async def infer(
        self,
        model_input: ModelInput,
        *,
        deadline: Deadline,
        cancel: CancelToken,
    ) -> ModelResponse: ...


@runtime_checkable
class ProviderSink(Protocol):
    """telemetry.EventLog 的结构子集，避免 providers 依赖具体实现。"""

    def emit(self, event: str, **fields: object) -> None: ...
