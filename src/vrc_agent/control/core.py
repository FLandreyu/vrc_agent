"""无阻塞控制内核。外部事件/进程/时钟的适配在 service 层完成。"""

from collections import OrderedDict
from typing import Callable

from vrc_agent.clock import Clock
from vrc_agent.config import ControlConfig
from vrc_agent.contracts import ActionRequest, ActionResult, Observation, new_id
from vrc_agent.transport.osc import MappedTransport

MS = 1_000_000


class Controller:
    def __init__(
        self,
        session_id: str,
        config: ControlConfig,
        transport: MappedTransport,
        clock: Clock,
        emit: Callable[..., None] | None = None,
    ):
        self.session_id = session_id
        self.config = config
        self.transport = transport
        self.clock = clock
        self.emit = emit or (lambda *args, **kwargs: None)
        self.state = "DISARMED"
        self.environment_epoch = 0
        self.lease_id: str | None = None
        self.lease_expires_ns = 0
        self.last_heartbeat_ns = clock.now_ns()
        self.observations: OrderedDict[str, Observation] = OrderedDict()
        self.results: dict[str, ActionResult] = {}
        self.active: ActionRequest | None = None
        self.active_observation: Observation | None = None
        self.active_until_ns = 0
        self.pending_releases = 0
        self.last_chat_ns: int | None = None
        self.reason = "OK"

    def heartbeat(self) -> None:
        self.last_heartbeat_ns = self.clock.now_ns()

    def arm(self, preflight_ok: bool) -> dict:
        self.tick()
        if self.state != "DISARMED" or self.pending_releases or not preflight_ok:
            raise ValueError("cannot arm: reset, wait for releases and pass preflight first")
        if self.clock.now_ns() - self.last_heartbeat_ns >= self.config.heartbeat_timeout_ms * MS:
            raise ValueError("cannot arm without a recent heartbeat")
        self.environment_epoch += 1
        self.observations.clear()
        self.lease_id = new_id()
        self.lease_expires_ns = self.clock.now_ns() + self.config.lease_ms * MS
        self.state = "ARMED_IDLE"
        self.reason = "OK"
        self.emit("armed", state=self.state)
        return self.snapshot()

    def reset(self) -> None:
        if self.state not in {"PAUSED", "ESTOP", "DISARMED"} or self.pending_releases:
            raise ValueError("stop and wait for releases before reset")
        self.state = "DISARMED"
        self.reason = "OK"
        self.lease_id = None
        self.observations.clear()

    def observe(self, observation: Observation) -> bool:
        now = self.clock.now_ns()
        if (
            observation.session_id != self.session_id
            or observation.environment_epoch != self.environment_epoch
            or observation.captured_at_ns > now
        ):
            return False
        existing = self.observations.get(observation.observation_id)
        if existing is not None:
            return existing == observation  # 不允许通过相同 ID 刷新旧观测。
        if observation.window_state != "visible" and self.state in {"ARMED_IDLE", "EXECUTING"}:
            self.stop("FOCUS_LOST")
        self.observations[observation.observation_id] = observation
        while len(self.observations) > 256:
            self.observations.popitem(last=False)
        return True

    def _result(
        self, request: ActionRequest, status: str, reason: str = "OK", started: int | None = None
    ) -> ActionResult:
        result = ActionResult(
            session_id=self.session_id,
            action_id=request.action_id,
            status=status,
            reason_code=reason,
            started_at_ns=started,
            finished_at_ns=None if status == "RUNNING" else self.clock.now_ns(),
            evidence_observation_ids=(request.based_on_observation_id,),
        )
        if (
            request.action_id in self.results
            or len(self.results) < self.config.max_actions_per_session
        ):
            self.results[request.action_id] = result
        self.emit("action_result", action_id=request.action_id, status=status, reason_code=reason)
        return result

    def _reject_reason(self, request: ActionRequest) -> str | None:
        now = self.clock.now_ns()
        if request.session_id != self.session_id:
            return "SESSION_MISMATCH"
        if request.environment_epoch != self.environment_epoch:
            return "EPOCH_MISMATCH"
        if self.state not in {"ARMED_IDLE", "EXECUTING"}:
            return "NOT_ARMED"
        if request.lease_id != self.lease_id or now >= self.lease_expires_ns:
            return "EXPIRED_LEASE"
        if request.issued_at_ns > now or request.expires_at_ns <= now:
            return "EXPIRED_ACTION"
        if request.kind == "stop":
            return None
        if self.pending_releases or self.active:
            return "BUSY"
        observation = self.observations.get(request.based_on_observation_id)
        if (
            observation is None
            or observation.window_state != "visible"
            or now - observation.captured_at_ns >= self.config.observation_max_age_ms * MS
        ):
            return "STALE_OBSERVATION"
        if request.kind in {"speak", "interact"}:
            return "UNSUPPORTED_CAPABILITY"
        if request.kind == "motion" and request.args.duration_ms > self.config.max_motion_ms:
            return "INVALID_ARGS"
        if request.kind == "avatar_set":
            spec = self.transport.config.avatar_parameters.get(request.args.parameter)
            if spec is None or not spec.accepts(request.args.value):
                return "UNSUPPORTED_CAPABILITY"
        if (
            request.kind == "chatbox"
            and self.last_chat_ns is not None
            and now - self.last_chat_ns < self.config.chat_interval_ms * MS
        ):
            return "RATE_LIMITED"
        return None

    def submit(self, request: ActionRequest) -> ActionResult:
        self.tick()
        # 会话边界先于幂等查询，避免旧会话的 ID 取到当前结果。
        if request.session_id != self.session_id:
            return ActionResult(
                session_id=self.session_id,
                action_id=request.action_id,
                status="REJECTED",
                reason_code="SESSION_MISMATCH",
            )
        if request.action_id in self.results:
            return self.results[request.action_id]
        if len(self.results) >= self.config.max_actions_per_session:
            self.stop("CAPACITY_REACHED")
            return self._result(request, "REJECTED", "CAPACITY_REACHED")
        if reason := self._reject_reason(request):
            return self._result(request, "REJECTED", reason)
        now = self.clock.now_ns()
        try:
            if request.kind == "stop":
                self.stop("MANUAL_STOP")
            elif request.kind in {"motion", "wait"}:
                self.active = request
                self.active_observation = self.observations[request.based_on_observation_id]
                self.active_until_ns = now + request.args.duration_ms * MS
                self.state = "EXECUTING"
                if request.kind == "motion":
                    self.transport.motion(request.args)
                return self._result(request, "RUNNING", started=now)
            elif request.kind == "chatbox":
                self.transport.chat(request.args)
                self.last_chat_ns = now
            elif request.kind == "avatar_set":
                self.transport.avatar(request.args.parameter, request.args.value)
            return self._result(request, "COMPLETED", started=now)
        except (OSError, ValueError):
            self.stop("TRANSPORT_ERROR")
            return self._result(request, "FAILED", "TRANSPORT_ERROR", started=now)

    def cancel(self, action_id: str) -> ActionResult | None:
        if self.active and self.active.action_id == action_id:
            self.stop("PREEMPTED")
        return self.results.get(action_id)

    def button_ready(self) -> bool:
        """按钮探针发送前的授权检查（复核 R1）：先 tick，再核对状态、租约与待归零。"""
        self.tick()
        now = self.clock.now_ns()
        return (
            self.state == "ARMED_IDLE"
            and self.active is None
            and not self.pending_releases
            and self.lease_id is not None
            and now < self.lease_expires_ns
        )

    def stop(self, reason: str = "MANUAL_STOP", emergency: bool = False) -> None:
        # 先释放再记录事件，记录不得成为停止前置条件。
        self.state = "ESTOP" if emergency or self.state == "ESTOP" else "PAUSED"
        self.reason = reason
        self.lease_id = None
        self.pending_releases = 2
        try:
            self.transport.neutralize()
            self.emit(
                "inputs_released",
                count=(3 if self.transport.motion_touched else 0)
                + len(self.transport.buttons)
                + len(self.transport.avatar_touched),
            )
        except OSError:
            self.reason = "TRANSPORT_ERROR"
        active, self.active = self.active, None
        self.active_observation = None
        if active:
            previous = self.results.get(active.action_id)
            self._result(
                active, "CANCELLED", reason, started=previous.started_at_ns if previous else None
            )
        self.emit("stopped", state=self.state, reason_code=self.reason)

    def tick(self) -> None:
        now = self.clock.now_ns()
        if self.pending_releases:
            try:
                self.transport.neutralize()
            except OSError:
                self.reason = "TRANSPORT_ERROR"
            self.pending_releases -= 1
        if self.state not in {"ARMED_IDLE", "EXECUTING"}:
            return
        if now - self.last_heartbeat_ns >= self.config.heartbeat_timeout_ms * MS:
            self.stop("HEARTBEAT_LOST")
            return
        if now >= self.lease_expires_ns:
            self.stop("EXPIRED_LEASE")
            return
        active = self.active
        if active is None:
            return
        if (
            active.kind == "motion"
            and self.active_observation is not None
            and now - self.active_observation.captured_at_ns
            >= self.config.observation_max_age_ms * MS
        ):
            self.stop("STALE_OBSERVATION")
            return
        deadline = min(self.active_until_ns, active.expires_at_ns)
        if now >= deadline:
            previous = self.results[active.action_id]
            try:
                if active.kind == "motion":
                    self.transport.release_motion()
            except OSError:
                self.stop("TRANSPORT_ERROR")
                return
            self.active = None
            self.active_observation = None
            self.state = "ARMED_IDLE"
            expired = active.expires_at_ns < self.active_until_ns
            self._result(
                active,
                "TIMED_OUT" if expired else "COMPLETED",
                "EXPIRED_ACTION" if expired else "OK",
                previous.started_at_ns,
            )

    def snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "state": self.state,
            "reason_code": self.reason,
            "environment_epoch": self.environment_epoch,
            "lease_id": self.lease_id,
            "lease_expires_ns": self.lease_expires_ns,
            "active_action_id": self.active.action_id if self.active else None,
            "action_count": len(self.results),
        }
