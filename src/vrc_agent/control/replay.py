"""仅离线：虚拟时钟驱动真实控制内核，不能接入 OSC/live transport。"""

from vrc_agent.clock import FakeClock
from vrc_agent.config import ControlConfig
from vrc_agent.control.core import Controller
from vrc_agent.transport.osc import RecordingTransport


class ReplayController:
    def __init__(self, session_id: str, clock: FakeClock, *, estop_at_ns: int | None = None):
        self.clock = clock
        self.session_id = session_id
        self.estop_at_ns = estop_at_ns
        self.messages: list[dict] = []
        self.events: list[dict] = []
        owner = self

        class Recorder(RecordingTransport):
            def send(self, address, values):
                owner.messages.append(
                    {"at_ns": clock.now_ns(), "address": address, "values": list(values)}
                )
                super().send(address, values)

        self._core = Controller(session_id, ControlConfig(), Recorder(), clock, self._emit)

    def _emit(self, event, **fields):
        self.events.append(
            {
                "schema_version": 1,
                "event_id": f"control-{len(self.events) + 1:04d}",
                "session_id": self.session_id,
                "at_ns": self.clock.now_ns(),
                "event": event,
                **fields,
            }
        )

    def arm(self):
        """只为本地离线模拟建立新租约；Runtime 不会在失败后重新启用。"""
        return self._core.arm(True)

    def sync(self):
        if self.estop_at_ns is not None and self.clock.now_ns() >= self.estop_at_ns:
            self.estop_at_ns = None
            self.stop(emergency=True)
        self._core.tick()

    def advance_to(self, at_ns: int):
        # 心跳与 tick 在时间跳跃期间持续运行，而非一次跨过动作期限。
        while self.clock.now_ns() < at_ns:
            self._core.heartbeat()
            self.clock.value = min(at_ns, self.clock.now_ns() + 10_000_000)
            self.sync()

    def snapshot(self):
        self.sync()
        return self._core.snapshot()

    def observe(self, observation):
        return self._core.observe(observation)

    def submit(self, request):
        self.sync()
        return self._core.submit(request)

    def result(self, action_id):
        self.sync()
        return self._core.results[action_id]

    def stop(self, reason="MANUAL_STOP", *, emergency=False):
        self._core.stop(reason, emergency=emergency)

    def close(self):
        self.stop()
        self._core.tick()
        self._core.tick()
