"""对话层的失败原因。

与 `CaptureFailure`/`ProviderFailure`/`PerceptionFailure` 一样，这里自带一套闭集：
`contracts.Reason` 只描述「动作为什么被拒」，不复用。
"""

from typing import Literal

#: 排队与播放可能出现的失败。全部是**可预期的运行状态**，不是编程错误。
DialogueFailure = Literal[
    "TEXT_INVALID",  # 文本为空、超长、含 NUL 或行数超限
    "QUEUE_FULL",  # 队列已满，且没有可淘汰的过期项
    "THROTTLED",  # 距上次发言不足最小间隔
    "EXPIRED",  # 轮到它时已经过期（不补播）
    "PAUSED",  # 半双工处于暂停/停止态，拒绝新发言
    "DEVICE_UNAVAILABLE",  # 配置的设备名匹配不到，或设备通道数不满足要求
    "STREAM_UNAVAILABLE",  # 音频后端打开流失败（驱动占用、采样率不支持等）
    "CANCELLED",  # 被本机停止或取消打断
    "UNSUPPORTED",  # 本版本不支持的组合（例如未启用的真实引擎）
]

DIALOGUE_FAILURES: tuple[DialogueFailure, ...] = (
    "TEXT_INVALID",
    "QUEUE_FULL",
    "THROTTLED",
    "EXPIRED",
    "PAUSED",
    "DEVICE_UNAVAILABLE",
    "STREAM_UNAVAILABLE",
    "CANCELLED",
    "UNSUPPORTED",
)


class DialogueError(RuntimeError):
    """对话层的受控失败通道；`detail` 里不得放真人语音内容或密钥。"""

    def __init__(self, failure: DialogueFailure, detail: str = "") -> None:
        if failure not in DIALOGUE_FAILURES:
            raise ValueError(f"unknown dialogue failure: {failure!r}")
        self.failure: DialogueFailure = failure
        self.detail = detail
        super().__init__(f"{failure}: {detail}" if detail else failure)
