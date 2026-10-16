"""把排队后的文本发到 chatbox：一个纯假实现，和一个接**真实控制内核**的适配器。

真实路径（`ControllerChatSender`）复用 T03 已有的授权链：绑定权限 → `Controller.submit`
→ `transport.chat()` → `/chatbox/input`。它需要一台已 `arm()` 的控制器和一个**新鲜且
visible** 的观测，因此离线测试可以只接 `RecordingTransport`，不打开任何 socket。
"""

from __future__ import annotations

from typing import Callable

from pydantic import ValidationError

from vrc_agent.clock import Clock, FakeClock
from vrc_agent.contracts import ActionProposal, Observation, new_id
from vrc_agent.control.core import Controller
from vrc_agent.control.service import bind_request
from vrc_agent.dialogue.errors import DialogueError


class SteppedControllerClock(FakeClock):
    """离线装配用：虚拟时间按 10 ms 小步前进，每步都给控制内核心跳与 tick。

    直接 `advance_ms(2000)` 会让 500 ms 心跳超时立刻把控制器打成 PAUSED——那是真实的
    watchdog 行为，不是 bug；离线装配必须像 T06 的 ReplayController 那样**分步**推进。
    """

    def __init__(
        self,
        controller: Controller | None,
        now_ns: int = 1_000_000_000,
        step_ms: int = 10,
    ) -> None:
        super().__init__(now_ns)
        #: 允许先建时钟再建控制器（控制器构造需要时钟），推进前必须接上。
        self.controller = controller
        self.step_ms = max(1, step_ms)

    def advance_ms(self, milliseconds: int) -> None:
        if milliseconds < 0:
            raise ValueError("clock cannot move backwards")
        if self.controller is None:
            raise DialogueError("UNSUPPORTED", "stepped clock has no controller attached")
        target = self.value + milliseconds * 1_000_000
        while self.value < target:
            self.controller.heartbeat()
            self.value = min(target, self.value + self.step_ms * 1_000_000)
            self.controller.tick()


class RecordingChatSender:
    """不发任何东西，只记录文本长度与顺序的假发送器（离线会话用）。"""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def __call__(self, text: str) -> str:
        self.sent.append(text)
        return "OK"


class ControllerChatSender:
    """经真实控制内核提交 chatbox 动作。

    返回控制层给出的状态字符串：`OK`（COMPLETED）或 `STATUS:REASON`，因此
    `RATE_LIMITED`、`NOT_ARMED`、`EXPIRED_LEASE` 这些拒绝会如实出现在会话记录里。
    控制层已经停下（没有租约）时**不构造请求**，直接回报拒绝，避免抛校验异常。
    """

    def __init__(
        self,
        controller: Controller,
        *,
        clock: Clock,
        ttl_ms: int = 1000,
        frame_size: tuple[int, int] = (1, 1),
        observation_factory: Callable[[], Observation] | None = None,
    ) -> None:
        self.controller = controller
        self.clock = clock
        self.ttl_ms = ttl_ms
        self.frame_size = frame_size
        self.observation_factory = observation_factory or self._default_observation
        self.attempts = 0
        self.rejected = 0

    def _default_observation(self) -> Observation:
        snapshot = self.controller.snapshot()
        return Observation(
            session_id=self.controller.session_id,
            observation_id=new_id(),
            captured_at_ns=self.clock.now_ns(),
            environment_epoch=snapshot["environment_epoch"],
            # chatbox 不看画面，但控制层要求「新鲜且 visible」的观测作为证据。
            frame_ref="dialogue-chatbox",
            frame_size=self.frame_size,
            window_state="visible",
        )

    def __call__(self, text: str) -> str:
        self.attempts += 1
        snapshot = self.controller.snapshot()
        if snapshot.get("lease_id") is None or snapshot.get("state") not in {
            "ARMED_IDLE",
            "EXECUTING",
        }:
            self.rejected += 1
            reason = snapshot.get("reason_code")
            return f"REJECTED:{reason if reason and reason != 'OK' else 'NOT_ARMED'}"
        observation = self.observation_factory()
        try:
            self.controller.observe(observation)
        except DialogueError:
            raise
        except Exception as error:  # 观测被拒（例如会话不匹配）时如实回报
            self.rejected += 1
            return f"OBSERVE_FAILED:{type(error).__name__}"
        proposal = ActionProposal(kind="chatbox", args={"text": text})
        try:
            request = bind_request(proposal, observation, snapshot, self.ttl_ms, clock=self.clock)
        except ValidationError as error:
            self.rejected += 1
            return f"REJECTED:INVALID_BINDING:{error.error_count()}"
        result = self.controller.submit(request)
        if result.status == "COMPLETED":
            return "OK"
        self.rejected += 1
        return f"{result.status}:{result.reason_code}"
