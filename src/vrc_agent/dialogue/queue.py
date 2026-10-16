"""聊天框发言队列：限长、节流、过期丢弃。

设计约束（ARCHITECTURE 第 6 节）：

- 队列**有界**；满了以后淘汰最旧的一条而不是丢掉最新的回复，因为对一段对话来说
  最新回复才相关，旧的即使补播也已经过时。
- 每条发言都有生存期；轮到它时若已过期就丢弃并计数，**不在恢复后补播旧回复**。
- 节流：距上次真正发出不足最小间隔时 `take()` 返回 None（尚未发送，不算丢弃）。
- 暂停/停止：清空待发内容并拒绝新发言，避免暂停期间积累一堆过期语音。
- 队列里只保留文本与时间戳；**不写盘、不进日志**，统计只有计数与原因码。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, ValidationError

from vrc_agent.clock import Clock, SystemClock
from vrc_agent.contracts import ChatArgs, Contract, Identifier, NanoTime, new_id
from vrc_agent.dialogue.errors import DialogueError

MS = 1_000_000

#: 发言来源：模型生成的回复，或本机操作者手动输入。
ChatSource = Literal["model", "operator"]


class ChatItem(Contract):
    """队列中的一条待发发言。只有文本与时间，没有音频、没有设备信息。"""

    item_id: Identifier = Field(default_factory=new_id)
    text: Annotated[str, Field(min_length=1, max_length=144)]
    source: ChatSource = "model"
    created_at_ns: NanoTime
    expires_at_ns: NanoTime


class ChatQueue:
    """有界、节流、会过期的发言队列。"""

    def __init__(
        self,
        *,
        session_id: Identifier = "dialogue",
        clock: Clock | None = None,
        max_pending: int = 8,
        ttl_ms: int = 20_000,
        min_interval_ms: int = 2_000,
    ) -> None:
        if not 1 <= max_pending <= 64:
            raise ValueError("max_pending must be between 1 and 64")
        if not 500 <= ttl_ms <= 600_000:
            raise ValueError("ttl_ms must be between 500 and 600000")
        if not 0 <= min_interval_ms <= 60_000:
            raise ValueError("min_interval_ms must be between 0 and 60000")
        self.session_id = session_id
        self.clock: Clock = clock or SystemClock()
        self.max_pending = max_pending
        self.ttl_ms = ttl_ms
        self.min_interval_ms = min_interval_ms
        self._pending: list[ChatItem] = []
        self._paused_reason: str | None = None
        self._last_sent_ns: int | None = None
        self.enqueued = 0
        self.sent = 0
        self.dropped_expired = 0
        self.dropped_full = 0
        self.rejected_paused = 0
        self.throttled = 0

    # ------------------------------------------------------------------ 入队

    def enqueue(
        self,
        text: str,
        *,
        source: ChatSource = "model",
        now_ns: int | None = None,
    ) -> ChatItem:
        """校验并入队；失败抛 `DialogueError`，不静默丢弃。"""
        now = self.clock.now_ns() if now_ns is None else now_ns
        try:
            # 复用 chatbox 契约的确定性长度/行数校验（144 个 UTF-16 单元、最多 9 行）。
            validated = ChatArgs(text=text)
        except ValidationError as error:
            raise DialogueError("TEXT_INVALID", error.errors()[0]["msg"]) from error
        if self._paused_reason is not None:
            self.rejected_paused += 1
            raise DialogueError("PAUSED", self._paused_reason)
        self._expire(now)
        if len(self._pending) >= self.max_pending:
            # 淘汰最旧的一条：它最可能已经过时，而新回复才是对话需要的。
            self._pending.pop(0)
            self.dropped_full += 1
        item = ChatItem(
            text=validated.text,
            source=source,
            created_at_ns=now,
            expires_at_ns=now + self.ttl_ms * MS,
        )
        self._pending.append(item)
        self.enqueued += 1
        return item

    # ------------------------------------------------------------------ 出队

    def take(self, *, now_ns: int | None = None) -> ChatItem | None:
        """取出一条可以立刻发送的发言。

        - 头部已过期 → 丢弃并计数（不返回、不补播）；
        - 距上次发送不足 `min_interval_ms` → 返回 None 并计一次节流；
        - 暂停中或队列空 → 返回 None。
        """
        now = self.clock.now_ns() if now_ns is None else now_ns
        if self._paused_reason is not None:
            return None
        self._expire(now)
        if not self._pending:
            return None
        if (
            self.min_interval_ms
            and self._last_sent_ns is not None
            and now - self._last_sent_ns < self.min_interval_ms * MS
        ):
            self.throttled += 1
            return None
        item = self._pending.pop(0)
        self._last_sent_ns = now
        self.sent += 1
        return item

    def peek(self, *, now_ns: int | None = None) -> ChatItem | None:
        """看一眼下一条（不消费），用于判断「值不值得等节流窗口」。"""
        now = self.clock.now_ns() if now_ns is None else now_ns
        self._expire(now)
        return self._pending[0] if self._pending else None

    def next_due_ns(self, *, now_ns: int | None = None) -> int | None:
        """还需要等多久才有资格发送（没有待发内容时返回 None）。"""
        now = self.clock.now_ns() if now_ns is None else now_ns
        if self._paused_reason is not None or not self._pending:
            return None
        if self.min_interval_ms == 0 or self._last_sent_ns is None:
            return now
        due = self._last_sent_ns + self.min_interval_ms * MS
        return max(now, due)

    # -------------------------------------------------------------- 暂停/恢复

    def pause(self, reason: str = "PAUSED") -> int:
        """进入暂停：清空待发并拒绝新发言，返回被清掉的数量。"""
        dropped = len(self._pending)
        self._pending.clear()
        self._paused_reason = reason
        return dropped

    def resume(self) -> None:
        self._paused_reason = None

    @property
    def paused(self) -> bool:
        return self._paused_reason is not None

    @property
    def pause_reason(self) -> str | None:
        return self._paused_reason

    # ------------------------------------------------------------------ 统计

    def _expire(self, now: int) -> None:
        while self._pending and self._pending[0].expires_at_ns <= now:
            self._pending.pop(0)
            self.dropped_expired += 1

    def stats(self) -> dict[str, int | str | None]:
        """只含计数与原因码：没有文本、没有音频。"""
        return {
            "pending": len(self._pending),
            "enqueued": self.enqueued,
            "sent": self.sent,
            "dropped_expired": self.dropped_expired,
            "dropped_full": self.dropped_full,
            "rejected_paused": self.rejected_paused,
            "throttled": self.throttled,
            "max_pending": self.max_pending,
            "ttl_ms": self.ttl_ms,
            "min_interval_ms": self.min_interval_ms,
            "pause_reason": self._pause_reason_text(),
        }

    def _pause_reason_text(self) -> str | None:
        # 原因码本身就是短标识符，可以直接出现在统计里。
        return self._paused_reason
