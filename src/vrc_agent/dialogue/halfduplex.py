"""半双工状态机：AI 播放时门控监听，暂停时不监听，急停后需要人工重置。

首版只做「播放或监听」二选一（ARCHITECTURE 第 6 节）：不做回声消除，因此 AI 说话
期间从麦克风采到的内容一律丢弃，避免把自己的声音当成用户输入。这不等于支持用户
语音打断——本机停止（急停/停止命令）始终可以打断播放，这一点由 `stop()` 与
`CancelToken` 保证。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vrc_agent.clock import Clock, SystemClock
from vrc_agent.contracts import Contract, NanoTime
from vrc_agent.dialogue.errors import DialogueError

HalfDuplexState = Literal["IDLE", "LISTENING", "SPEAKING", "PAUSED", "STOPPED"]

#: 允许的迁移：状态 → 可由哪些操作到达。
_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "IDLE": ("begin_listening", "begin_speaking", "pause", "stop"),
    "LISTENING": ("begin_speaking", "pause", "stop"),
    "SPEAKING": ("finish_speaking", "pause", "stop"),
    "PAUSED": ("resume", "stop"),
    "STOPPED": ("reset",),
}


class HalfDuplexTransition(Contract):
    """一次状态迁移。只记状态与原因码，不含任何音频或文本。"""

    at_ns: NanoTime
    previous: str
    state: str
    reason: str


class HalfDuplex:
    def __init__(
        self,
        *,
        clock: Clock | None = None,
        gate_while_speaking: bool = True,
        max_transitions: int = 256,
    ) -> None:
        self.clock: Clock = clock or SystemClock()
        self.gate_while_speaking = gate_while_speaking
        self.max_transitions = max_transitions
        self._state: HalfDuplexState = "IDLE"
        self._transitions: list[HalfDuplexTransition] = []
        self.gated_captures = 0
        self.capture_blocks = 0

    # ------------------------------------------------------------------ 状态

    @property
    def state(self) -> HalfDuplexState:
        return self._state

    @property
    def transitions(self) -> tuple[HalfDuplexTransition, ...]:
        return tuple(self._transitions)

    @property
    def can_listen(self) -> bool:
        """只有 LISTENING 状态接受麦克风内容。"""
        return self._state == "LISTENING"

    @property
    def capture_gated(self) -> bool:
        """当前是否应当丢弃采集到的内容（播放中，或根本没在监听）。"""
        return not self.can_listen

    def note_gated_capture(self, blocks: int = 1) -> None:
        """记录被门控丢掉的音频块数（不保存内容本身）。"""
        self.gated_captures += blocks

    def note_capture_blocks(self, blocks: int) -> None:
        self.capture_blocks += blocks

    # ------------------------------------------------------------ 迁移操作

    def begin_listening(self) -> HalfDuplexState:
        return self._move("begin_listening", "LISTENING", "user_speech_window")

    def begin_speaking(self) -> HalfDuplexState:
        return self._move("begin_speaking", "SPEAKING", "ai_speaking")

    def finish_speaking(self) -> HalfDuplexState:
        return self._move("finish_speaking", "LISTENING", "ai_finished")

    def pause(self, reason: str = "PAUSED") -> HalfDuplexState:
        """暂停对话（例如控制层进入 PAUSED、或操作者手动暂停）。"""
        return self._move("pause", "PAUSED", reason)

    def resume(self) -> HalfDuplexState:
        return self._move("resume", "LISTENING", "resumed")

    def stop(self, reason: str = "MANUAL_STOP") -> HalfDuplexState:
        """本机停止（急停/停止命令）：终态，只能由 `reset()` 清除。"""
        return self._move("stop", "STOPPED", reason)

    def reset(self) -> HalfDuplexState:
        return self._move("reset", "IDLE", "reset")

    def _move(self, action: str, target: HalfDuplexState, reason: str) -> HalfDuplexState:
        if action not in _TRANSITIONS[self._state]:
            raise DialogueError(
                "PAUSED" if self._state in {"PAUSED", "STOPPED"} else "UNSUPPORTED",
                f"cannot {action} from {self._state}",
            )
        previous = self._state
        self._state = target
        self._record(previous, target, reason)
        return target

    def _record(self, previous: str, state: str, reason: str) -> None:
        self._transitions.append(
            HalfDuplexTransition(
                at_ns=self.clock.now_ns(), previous=previous, state=state, reason=reason
            )
        )
        if len(self._transitions) > self.max_transitions:
            del self._transitions[: len(self._transitions) - self.max_transitions]

    # ------------------------------------------------------------------ 统计

    def stats(self) -> dict[str, int | str]:
        return {
            "state": self._state,
            "transitions": len(self._transitions),
            "gated_captures": self.gated_captures,
            "capture_blocks": self.capture_blocks,
            "gate_while_speaking": int(self.gate_while_speaking),
        }


@dataclass(frozen=True, slots=True)
class ListeningWindow:
    """一次监听窗口的结果汇总：只有时长与块数，**不保存任何音频**。"""

    blocks: int
    blocked_ms: int
    gated_blocks: int
