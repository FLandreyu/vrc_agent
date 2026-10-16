"""spawn 独立控制进程；继承管道是唯一动作入口，IPC I/O 不阻塞 watchdog。"""

import json
import multiprocessing as mp
import os
import queue
import threading
import time
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from vrc_agent.clock import Clock, SystemClock
from vrc_agent.config import AppConfig
from vrc_agent.contracts import (
    ActionProposal,
    ActionRequest,
    ActionResult,
    Contract,
    Identifier,
    Observation,
    new_id,
)
from vrc_agent.control.core import Controller
from vrc_agent.control.platform import InstanceLock, StopSignal, WindowsControls
from vrc_agent.telemetry import EventLog
from vrc_agent.transport.osc import OscTransport, RecordingTransport

# T00 人工探针：键位由 WindowsControls 注册，每个按键在已启用状态各发一次短动作。
# yaw 在 0.2×200ms 下人工几乎观察不到，右/左转均使用契约上限强度与时长以便观察。
PROBE_MOTIONS: dict[str, dict] = {
    "probe_forward": {"forward": 0.2, "duration_ms": 200},
    "probe_backward": {"forward": -0.2, "duration_ms": 200},
    "probe_yaw": {"yaw": 1.0, "duration_ms": 500},
    "probe_yaw_left": {"yaw": -1.0, "duration_ms": 500},
    "probe_strafe_left": {"strafe": -0.2, "duration_ms": 200},
    "probe_strafe_right": {"strafe": 0.2, "duration_ms": 200},
}

# T00 交互探针：F3=UseRight（E 类交互）、F1=GrabRight（左键类抓取）、F11=DropRight（丢下），
# 按下后由主循环在 300ms 时释放；F2 发送固定聊天文本。
PROBE_BUTTONS = {
    "probe_use": "UseRight",
    "probe_grab": "GrabRight",
    "probe_drop": "DropRight",
}
PROBE_BUTTON_HOLD_S = 0.3
PROBE_CHAT_TEXT = "[vrc-agent] T00 chat probe"


class Command(Contract):
    request_id: Identifier
    operation: Literal[
        "heartbeat",
        "observe",
        "submit",
        "arm",
        "reset",
        "pause",
        "estop",
        "cancel",
        "status",
        "result",
        "shutdown",
    ]
    payload: dict = Field(default_factory=dict)


def bind_request(
    proposal: ActionProposal,
    observation: Observation,
    snapshot: dict,
    ttl_ms: int = 1000,
    *,
    clock: Clock | None = None,
) -> ActionRequest:
    """可信运行时封装权限，不能直接把模型输出作为 Request 使用。"""
    now = (clock or SystemClock()).now_ns()
    return ActionRequest(
        **proposal.model_dump(),
        session_id=snapshot["session_id"],
        lease_id=snapshot["lease_id"],
        environment_epoch=snapshot["environment_epoch"],
        based_on_observation_id=observation.observation_id,
        issued_at_ns=now,
        expires_at_ns=now + ttl_ms * 1_000_000,
    )


def probe_log_result(log: EventLog, event: str, result: ActionResult) -> None:
    """按 submit 终态记录探针动作（复核 R3/R3b）：只有成功派发才记 probe_dispatched。"""
    if result.status == "REJECTED":
        log.emit(
            "probe_rejected",
            operation=event,
            action_id=result.action_id,
            reason_code=result.reason_code,
        )
    elif result.status in {"RUNNING", "COMPLETED"}:
        log.emit("probe_dispatched", operation=event, action_id=result.action_id)
    else:
        # FAILED 等发送/执行失败：单独记录，不作为成功派发。
        log.emit(
            "probe_send_failed",
            operation=event,
            action_id=result.action_id,
            reason_code=result.reason_code,
        )


def _receive(connection, inbox: queue.Queue, disconnected: threading.Event) -> None:
    try:
        while True:
            raw = connection.recv_bytes(maxlength=65536)
            command = Command.model_validate_json(raw)
            inbox.put_nowait(command)
    except (EOFError, OSError, ValueError, queue.Full):
        disconnected.set()


def _send(connection, outbox: queue.Queue, disconnected: threading.Event) -> None:
    try:
        while True:
            message = outbox.get()
            if message is None:
                return
            connection.send_bytes(json.dumps(message).encode())
    except (EOFError, OSError, ValueError):
        disconnected.set()


def _worker(connection, config_json: str, state_path: str, session_id: str) -> None:
    state_dir = Path(state_path)
    lock = signal = controls = transport = core = log = None
    outbox: queue.Queue = queue.Queue(maxsize=64)
    inbox: queue.Queue = queue.Queue(maxsize=64)
    disconnected = threading.Event()
    startup_stage = "configuration"
    sender = threading.Thread(target=_send, args=(connection, outbox, disconnected), daemon=True)
    sender.start()

    def respond(message: dict) -> None:
        try:
            outbox.put_nowait(message)
        except queue.Full:
            disconnected.set()

    try:
        config = AppConfig.model_validate_json(config_json)
        live = config.mode == "live"
        startup_stage = "lock"
        lock = InstanceLock(state_dir, config.osc.send_port if live else None)
        startup_stage = "stop_signal"
        signal = StopSignal(state_dir)
        # 热键注册失败必须在创建 live transport 前退出。
        if live:
            startup_stage = "hotkeys"
            controls = WindowsControls(
                config.control.window_title, config.control.enable_motion_probe
            )
        startup_stage = "osc"
        transport = OscTransport(config.osc) if live else RecordingTransport(config.osc)
        startup_stage = "log"
        log = EventLog(state_dir / f"{session_id}.jsonl", session_id)
        core = Controller(session_id, config.control, transport, SystemClock(), log.emit)
        log.emit("controller_started", pid=os.getpid(), mode=config.mode)
        threading.Thread(
            target=_receive, args=(connection, inbox, disconnected), daemon=True
        ).start()
        respond({"request_id": "ready", "ok": True, "data": core.snapshot()})
        startup_stage = "runtime"
        exiting_at: float | None = None
        avatar_valid = True
        parameters: dict = {}
        pending_button_releases: dict[str, float] = {}
        while True:
            loop_start = time.monotonic()
            if disconnected.is_set() and exiting_at is None:
                core.stop("HEARTBEAT_LOST", emergency=True)
                exiting_at = loop_start + 0.1
            if signal.triggered():
                core.stop("MANUAL_STOP", emergency=True)
            if controls:
                # 急停同一 tick 优先处理，禁止按键组合在停止后立即重新启用。
                events = controls.events()
                if "estop" in events:
                    core.stop("MANUAL_STOP", emergency=True)
                    events = []
                if core.state in {"ARMED_IDLE", "EXECUTING"} and not controls.focus_ok():
                    core.stop("FOCUS_LOST")
                for event in events:
                    try:
                        if event == "arm":
                            core.arm(avatar_valid and controls.focus_ok(bind=True))
                        elif event == "reset":
                            core.reset()
                        elif event in PROBE_MOTIONS:
                            if (
                                not pending_button_releases
                                and core.state == "ARMED_IDLE"
                                and controls.focus_ok()
                            ):
                                observation = Observation(
                                    session_id=session_id,
                                    captured_at_ns=time.monotonic_ns(),
                                    environment_epoch=core.environment_epoch,
                                    window_state="visible",
                                )
                                core.observe(observation)
                                proposal = ActionProposal.model_validate(
                                    {
                                        "kind": "motion",
                                        "args": PROBE_MOTIONS[event],
                                        "expected_outcome": (
                                            "manual input probe; no visual verification"
                                        ),
                                    }
                                )
                                result = core.submit(
                                    bind_request(proposal, observation, core.snapshot())
                                )
                                probe_log_result(log, event, result)
                            else:
                                # 未启用、焦点不符或按钮按住期间也留痕，避免探针按键被静默忽略。
                                log.emit("human_command_rejected", operation=event)
                        elif event == "probe_chat":
                            if (
                                not pending_button_releases
                                and core.state == "ARMED_IDLE"
                                and controls.focus_ok()
                            ):
                                observation = Observation(
                                    session_id=session_id,
                                    captured_at_ns=time.monotonic_ns(),
                                    environment_epoch=core.environment_epoch,
                                    window_state="visible",
                                )
                                core.observe(observation)
                                proposal = ActionProposal.model_validate(
                                    {
                                        "kind": "chatbox",
                                        "args": {"text": PROBE_CHAT_TEXT},
                                        "expected_outcome": (
                                            "manual chat probe; no visual verification"
                                        ),
                                    }
                                )
                                result = core.submit(
                                    bind_request(proposal, observation, core.snapshot())
                                )
                                probe_log_result(log, event, result)
                            else:
                                log.emit("human_command_rejected", operation=event)
                        elif event in PROBE_BUTTONS:
                            button = PROBE_BUTTONS[event]
                            if button in pending_button_releases:
                                # 复核 R2：未释放前拒绝新脉冲，禁止连按延长按下时间。
                                log.emit("human_command_rejected", operation=event)
                            elif core.button_ready() and controls.focus_ok():
                                # 复核 R1：发送前经控制层检查（tick 后的状态/租约/待归零）。
                                try:
                                    transport.button(button, True)
                                except OSError:
                                    core.stop("TRANSPORT_ERROR")
                                else:
                                    pending_button_releases[button] = (
                                        time.monotonic() + PROBE_BUTTON_HOLD_S
                                    )
                                    log.emit("probe_dispatched", operation=event)
                            else:
                                log.emit("human_command_rejected", operation=event)
                    except ValueError:
                        log.emit("human_command_rejected", operation=event)
            try:
                for event in transport.poll():
                    if event["kind"] == "avatar_change":
                        core.stop("EPOCH_MISMATCH")
                        avatar_valid = False  # 必须重新验证配置并重启，不能只按 F9 恢复。
                        parameters.clear()
                    else:
                        parameters[event["name"]] = event["sample"]
                        # T00 参数接收观测：记录名称与值，便于人工对账（值域由白名单校验）。
                        log.emit(
                            "parameter_received",
                            operation=event["name"],
                            value=event["sample"]["value"],
                        )
            except OSError:
                core.stop("TRANSPORT_ERROR")
            core.tick()
            for _ in range(8):
                try:
                    command = inbox.get_nowait()
                except queue.Empty:
                    break
                if command.operation == "heartbeat":
                    # 排队太久的心跳不应在父进程停顿后续命。
                    sent = command.payload.get("sent_at_ns")
                    age = time.monotonic_ns() - sent if type(sent) is int else -1
                    if 0 <= age < config.control.heartbeat_timeout_ms * 1_000_000:
                        core.heartbeat()
                    continue
                try:
                    op, payload = command.operation, command.payload
                    data = None
                    if op in {"arm", "reset"} and live:
                        raise ValueError("live ARM/RESET requires local hotkeys")
                    if exiting_at is not None and op not in {"status", "shutdown", "estop"}:
                        raise ValueError("controller is shutting down")
                    if op == "arm":
                        data = core.arm(True)
                    elif op == "reset":
                        core.reset()
                    elif op == "observe":
                        data = core.observe(Observation.model_validate_json(json.dumps(payload)))
                    elif op == "submit":
                        data = core.submit(ActionRequest.model_validate(payload)).model_dump(
                            mode="json"
                        )
                    elif op == "cancel":
                        result = core.cancel(payload["action_id"])
                        data = result.model_dump(mode="json") if result else None
                    elif op in {"pause", "estop", "shutdown"}:
                        core.stop("MANUAL_STOP", emergency=op != "pause")
                        if op == "shutdown":
                            exiting_at = time.monotonic() + 0.1
                    elif op == "result":
                        result = core.results.get(payload["action_id"])
                        data = result.model_dump(mode="json") if result else None
                    elif op == "status":
                        data = {
                            **core.snapshot(),
                            "parameters": parameters,
                            "log_failed": log.failed,
                            "log_dropped": log.dropped,
                        }
                        if isinstance(transport, RecordingTransport):
                            data["recorded_messages"] = list(transport.messages)[-32:]
                            data["sent_count"] = transport.sent_count
                    respond({"request_id": command.request_id, "ok": True, "data": data})
                except (ValidationError, ValueError, KeyError, TypeError):
                    respond(
                        {
                            "request_id": command.request_id,
                            "ok": False,
                            "error": "INVALID_COMMAND_OR_STATE",
                        }
                    )
            now_btn = time.monotonic()
            for button, release_at in list(pending_button_releases.items()):
                if now_btn >= release_at:
                    del pending_button_releases[button]
                    try:
                        transport.button(button, False)
                    except OSError:
                        # 复核 R3：释放失败如实记录；core 的重复归零路径会继续重试。
                        core.stop("TRANSPORT_ERROR")
                        log.emit("probe_release_failed", operation=button)
                    else:
                        log.emit("probe_released", operation=button)
            core.tick()
            if exiting_at is not None and time.monotonic() >= exiting_at:
                break
            time.sleep(max(0.0, 1 / config.control.tick_hz - (time.monotonic() - loop_start)))
    except Exception as exc:
        respond(
            {
                "request_id": "ready",
                "ok": False,
                "error": type(exc).__name__,
                "detail": startup_failure_detail(exc, startup_stage),
            }
        )
    finally:
        if core:
            core.stop("MANUAL_STOP", emergency=True)
            for _ in range(2):
                core.tick()
            if log:
                log.emit("controller_exited", state=core.state, pid=os.getpid())
        if transport:
            transport.close()
        if controls:
            controls.close()
        if signal:
            signal.close()
        if lock:
            lock.close()
        if log:
            log.close()
        try:
            outbox.put_nowait(None)
        except queue.Full:
            pass
        sender.join(timeout=0.2)
        connection.close()


def startup_failure_detail(exc: Exception, stage: str) -> str:
    """只透出已知诊断，不原样回显可能包含私密配置/路径的任意异常文本。"""
    known = {
        "another live controller owns this OSC destination",
        "required global hotkey is already in use",
        "live mode requires Windows",
    }
    if str(exc) in known:
        return str(exc)
    descriptions = {
        "lock": "state directory lock or OSC destination is already owned or inaccessible",
        "hotkeys": "local hotkey registration failed; check hotkey conflicts",
        "osc": "OSC socket initialization failed; check send/receive port conflicts",
        "stop_signal": "independent stop signal initialization failed",
        "log": "controller log could not be opened",
        "configuration": "invalid controller configuration",
        "runtime": "controller runtime failure",
    }
    code = getattr(exc, "winerror", None) or getattr(exc, "errno", None)
    return f"{descriptions.get(stage, 'controller initialization failed')} (stage={stage}, code={code})"


class ControllerProcess:
    def __init__(self, config: AppConfig, state_dir: Path, heartbeat: bool = True):
        self.config = config
        self.state_dir = state_dir.resolve()
        self.session_id = new_id()
        self.context = mp.get_context("spawn")
        self.connection, child = self.context.Pipe(duplex=True)
        self.process = self.context.Process(
            target=_worker,
            args=(child, config.model_dump_json(), str(self.state_dir), self.session_id),
            daemon=False,
            name="vrc-agent-controller",
        )
        self.lock = threading.Lock()
        self.stop_heartbeat = threading.Event()
        self.heartbeat_thread = None
        self.process.start()
        child.close()
        try:
            if not self.connection.poll(10):
                raise TimeoutError("controller startup timed out")
            ready = json.loads(self.connection.recv_bytes(65536))
            if not ready.get("ok"):
                raise RuntimeError(
                    f"controller unavailable: {ready.get('error')}: {ready.get('detail', 'no startup detail')}"
                )
            self.initial_status = ready["data"]
            if heartbeat:
                self.heartbeat_thread = threading.Thread(target=self._heartbeats, daemon=True)
                self.heartbeat_thread.start()
        except BaseException:
            self.connection.close()
            self.process.join(timeout=2)
            raise

    def _heartbeats(self) -> None:
        interval = self.config.control.heartbeat_interval_ms / 1000
        while not self.stop_heartbeat.is_set():
            try:
                with self.lock:
                    command = Command(
                        request_id=new_id(),
                        operation="heartbeat",
                        payload={"sent_at_ns": time.monotonic_ns()},
                    )
                    self.connection.send_bytes(command.model_dump_json().encode())
            except (EOFError, OSError, ValueError):
                return
            self.stop_heartbeat.wait(interval)

    def request(self, operation: str, payload: dict | None = None, timeout: float = 2.0):
        command = Command(request_id=new_id(), operation=operation, payload=payload or {})
        with self.lock:
            self.connection.send_bytes(command.model_dump_json().encode())
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not self.connection.poll(max(0, deadline - time.monotonic())):
                    break
                response = json.loads(self.connection.recv_bytes(65536))
                if response.get("request_id") == command.request_id:
                    if not response.get("ok"):
                        raise ValueError(response.get("error", "controller rejected command"))
                    return response.get("data")
            raise TimeoutError("controller did not respond; watchdog will stop without heartbeat")

    def close(self) -> None:
        self.stop_heartbeat.set()
        try:
            if self.process.is_alive():
                self.request("shutdown")
        except (EOFError, OSError, ValueError, TimeoutError):
            pass
        self.connection.close()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=1)
        self.process.join(timeout=3)
        if self.process.is_alive():
            raise RuntimeError("controller still alive; use the independent stop command")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
