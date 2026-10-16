"""默认离线演示；live 只能由本机热键启用。"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

from pydantic import ValidationError

from vrc_agent import __version__
from vrc_agent.config import AppConfig, load_config
from vrc_agent.contracts import Observation, new_id
from vrc_agent.control.platform import signal_stop
from vrc_agent.control.service import ControllerProcess, bind_request
from vrc_agent.providers.factory import describe_provider
from vrc_agent.providers.fake import FakeProvider


def print_json(data) -> None:
    rendered = json.dumps(data, ensure_ascii=False, indent=2)
    try:
        rendered.encode(getattr(sys.stdout, "encoding", None) or "utf-8")
    except UnicodeEncodeError:
        rendered = json.dumps(data, ensure_ascii=True, indent=2)
    print(rendered)


def demo(client: ControllerProcess) -> dict:
    snapshot = client.request("arm")
    observation = Observation(
        session_id=client.session_id,
        captured_at_ns=time.monotonic_ns(),
        environment_epoch=snapshot["environment_epoch"],
        window_state="visible",
    )
    client.request("observe", observation.model_dump(mode="json"))
    proposal = asyncio.run(FakeProvider().infer())
    action = bind_request(proposal, observation, snapshot)
    result = client.request("submit", action.model_dump(mode="json"))
    deadline = time.monotonic() + 2
    while result["status"] == "RUNNING" and time.monotonic() < deadline:
        time.sleep(0.03)
        result = client.request("result", {"action_id": action.action_id})
    status = client.request("status")
    return {
        "mode": "dry-run",
        "provider": "fake",
        "result": result,
        "recorded_messages": status["recorded_messages"],
        "note": "No UDP output. This is an input-control demo, not visual navigation.",
    }


def interactive(client: ControllerProcess) -> None:
    print("dry-run commands: arm, pulse, stop, reset, status, quit")
    while True:
        command = input("> ").strip().lower()
        if command in {"quit", "exit"}:
            return
        try:
            if command == "pulse":
                snapshot = client.request("status")
                observation = Observation(
                    session_id=client.session_id,
                    captured_at_ns=time.monotonic_ns(),
                    environment_epoch=snapshot["environment_epoch"],
                    window_state="visible",
                )
                client.request("observe", observation.model_dump(mode="json"))
                action = bind_request(asyncio.run(FakeProvider().infer()), observation, snapshot)
                print_json(client.request("submit", action.model_dump(mode="json")))
            elif command in {"arm", "reset", "status", "stop"}:
                print_json(client.request("estop" if command == "stop" else command))
            else:
                print("unknown command")
        except (ValueError, TimeoutError) as exc:
            print(type(exc).__name__ + ": command rejected; inspect status and reset if stopped")


def capture_awareness() -> str:
    """只读查询进程 DPI 感知级别；非 Windows 返回 "unavailable"。"""
    from vrc_agent.capture.win32 import awareness

    return awareness()


class RecordingSink:
    """把采集事件留在内存里（不落盘），让现场冒烟能直接看到失败原因码。"""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **fields: object) -> None:
        self.events.append({"event": event, **fields})


def capture_smoke(
    config: AppConfig, *, frames: int, interval_ms: int, epoch: int, session_id: str
) -> dict:
    """live 只读冒烟：只报元数据与统计，画面留在内存里，不落盘、不发 OSC。

    采集失败一律如实报告（window_state 非 visible 且 frame_ref 为 None），
    绝不会把上一帧当作新帧返回。
    """
    from vrc_agent.capture.live import live_capture
    from vrc_agent.capture.win32 import enumerate_windows

    sink = RecordingSink()
    source = live_capture(session_id=session_id, config=config, environment_epoch=epoch, sink=sink)
    readings: list[dict] = []
    try:
        for index in range(frames):
            if index and interval_ms:
                time.sleep(interval_ms / 1000)
            observation = source.next_observation()
            frame = source.frame_for(observation)
            readings.append(
                {
                    "observation_id": observation.observation_id,
                    "window_state": observation.window_state,
                    "captured_at_ns": observation.captured_at_ns,
                    "frame_size": list(observation.frame_size) if observation.frame_size else None,
                    "frame_ref": observation.frame_ref,
                    "frame_sha256_prefix": frame.digest()[:16] if frame is not None else None,
                }
            )
        stats = source.stats().as_dict()
    finally:
        source.close()
    return {
        "mode": "live-read-only",
        "sends_input": False,
        "writes_media": False,
        "session_id": session_id,
        "environment_epoch": epoch,
        "window_title": config.capture_window_title(),
        "window_class": config.capture.window_class,
        "require_unoccluded": config.capture.require_unoccluded,
        "dpi_awareness": capture_awareness(),
        "readings": readings,
        "events": sink.events,
        "stats": stats,
        "windows": [dict(item) for item in enumerate_windows(10)],
        "note": (
            "Read-only capture smoke test: frames stay in memory, no OSC input is sent, and a "
            "failed capture never returns the previous frame."
        ),
    }


def replay_run(fixture_path: Path | None) -> dict:
    """跑确定性回放素材：虚拟时钟、不落盘、耗尽即停（不循环、不填充）。"""
    from vrc_agent.capture import (
        ReplaySource,
        collect,
        default_replay_source,
        load_fixture,
        sequence_digest,
    )

    if fixture_path is None:
        source = default_replay_source()
    else:
        source = ReplaySource(load_fixture(fixture_path))
    records = collect(source)
    return {
        "mode": "replay",
        "fixture": source.fixture.name,
        "session_id": source.fixture.session_id,
        "environment_epoch": source.fixture.environment_epoch,
        "observations": [
            {
                "observation_id": observation.observation_id,
                "window_state": observation.window_state,
                "captured_at_ns": observation.captured_at_ns,
                "frame_size": list(observation.frame_size) if observation.frame_size else None,
                "frame_ref": observation.frame_ref,
                "frame_sha256_prefix": frame.digest()[:16] if frame is not None else None,
            }
            for observation, frame in records
        ],
        "sequence_digest": sequence_digest(records),
        "stats": source.stats().as_dict(),
        "note": (
            "Deterministic replay on a virtual clock: the same fixture always yields the same "
            "sequence, and nothing is written to disk."
        ),
    }


def perceive_run(
    config: AppConfig,
    *,
    fixture_path: Path | None,
    max_steps: int,
    provider_name: str | None,
    allow_paid: bool,
    send_images: bool,
) -> dict:
    """跑感知链路：回放观测 → 模型 → 受约束的 SceneState。

    默认是离线假模型（免费、无网络）。真实供应商必须同时满足三件事才会被调用：
    配置里 `provider` 是付费的、配置了正预算、并且命令行显式给了 `--allow-paid`。
    画面默认不发送（`--send-images` 才会把内存里的帧交给适配器）。
    """
    from vrc_agent.capture import (
        ReplaySource,
        default_fixture,
        load_fixture,
        parse_fixture,
    )
    from vrc_agent.capture.replay import ReplayExhausted
    from vrc_agent.clock import FakeClock
    from vrc_agent.config import PerceptionConfig
    from vrc_agent.perception import Perception
    from vrc_agent.providers.factory import (
        build_ledger,
        build_provider,
        describe_provider,
        provider_is_paid,
    )

    changes: dict = {}
    if provider_name is not None:
        changes["provider"] = provider_name
    if send_images:
        changes["send_images"] = True
    perception_config = (
        config.perception
        if not changes
        else PerceptionConfig.model_validate({**config.perception.model_dump(), **changes})
    )
    if provider_is_paid(perception_config) and not allow_paid:
        raise ValueError(
            f"provider {perception_config.provider!r} is paid and sends requests off this machine; "
            "re-run with --allow-paid to confirm"
        )
    sink = RecordingSink()
    ledger = build_ledger(perception_config)
    provider = build_provider(perception_config, ledger=ledger, sink=sink)
    # 感知与回放共用同一个虚拟时钟，这样「观测年龄」在离线回放里也有意义。
    fixture = (
        parse_fixture(default_fixture()) if fixture_path is None else load_fixture(fixture_path)
    )
    clock = FakeClock(fixture.start_ns)
    source = ReplaySource(fixture, clock=clock, sink=sink)
    perception = Perception(
        provider,
        clock=clock,
        max_entities=perception_config.max_scene_entities,
        max_observation_age_ms=perception_config.max_observation_age_ms,
        sink=sink,
    )
    scenes: list[dict] = []
    stopped_reason = "max_steps"
    try:
        for _ in range(max_steps):
            observation = source.next_observation()
            frame = source.frame_for(observation)
            scene = asyncio.run(perception.interpret(observation, frame=frame))
            scenes.append(
                {
                    "scene_id": scene.scene_id,
                    "observation_id": observation.observation_id,
                    "window_state": observation.window_state,
                    "generated_at_ns": scene.generated_at_ns,
                    "confidence": scene.confidence,
                    "entities": [
                        {
                            "track_id": entity.track_id,
                            "label": entity.label,
                            "bbox": list(entity.bbox),
                            "confidence": entity.confidence,
                            "evidence_observation_id": entity.evidence_observation_id,
                        }
                        for entity in scene.entities
                    ],
                    "hazards": list(scene.hazards),
                    "uncertainties": list(scene.uncertainties),
                }
            )
    except ReplayExhausted:
        stopped_reason = "replay_exhausted"
    return {
        "mode": "perceive-offline",
        "produces_actions": False,
        "sends_input": False,
        "writes_media": False,
        "fixture": source.fixture.name,
        "environment_epoch": source.fixture.environment_epoch,
        "provider": describe_provider(perception_config, ledger=ledger),
        "stopped_reason": stopped_reason,
        "scenes": scenes,
        "usage": ledger.snapshot(),
        "perception": perception.stats(),
        "capture": source.stats().as_dict(),
        "events": sink.events,
        "note": (
            "Perception turns observations into constrained scenes: entities must cite this "
            "observation as evidence, unusable model output becomes an unknown scene, and no "
            "action, lease or OSC message can come out of this path."
        ),
    }


def dialogue_run(
    config: AppConfig,
    *,
    scenario: str = "turns",
    turns: int | None = None,
    live_audio: bool = False,
    with_chatbox: bool = False,
    output_device: str | None = None,
    input_device: str | None = None,
    sample_rate: int | None = None,
    channels: int | None = None,
    block_ms: int | None = None,
    state_dir: Path = Path(".runtime"),
    sink=None,
) -> dict:
    """T07 交流冒烟：离线跑队列/半双工/合成语音/回流诊断。

    默认**不打开任何音频设备、不播放任何声音**；只有显式 `--live-audio` 才会碰真实设备，
    而且每次都把「碰了真设备」写进报告。`--devices` 是只读枚举（也不打开流）。
    """
    import threading

    from vrc_agent.clock import FakeClock, SystemClock
    from vrc_agent.dialogue import (
        ChatQueue,
        DialogueSession,
        FakeAsr,
        FakeBackend,
        FakeTts,
        HalfDuplex,
        QuietAsr,
        SoundDeviceBackend,
        describe_devices,
        run_echo_check,
        select_device,
        virtual_mic_hint,
    )

    dialogue = config.dialogue
    rate = sample_rate or dialogue.sample_rate
    chans = channels or dialogue.playback_channels
    block = block_ms or dialogue.block_ms
    planned_turns = turns or dialogue.turns
    notes: list[str] = []

    if scenario == "devices":
        devices = SoundDeviceBackend().list_devices()
        return {
            "mode": "dialogue-devices",
            "saves_media": False,
            "opens_stream": False,
            "audio_backend": "sounddevice",
            "device_count": len(devices),
            "devices": describe_devices(devices),
            "virtual_mic": virtual_mic_hint(devices),
            "note": (
                "read-only enumeration: no stream is opened, no sound is played, "
                "nothing is recorded"
            ),
        }

    clock = SystemClock() if live_audio else FakeClock()

    # --with-chatbox 要先建控制内核与「分步时钟」：后端与会话必须共用同一个时钟实例，
    # 否则虚拟时间会分成两条线（表现为时长全 0、心跳对不上）。
    chat_sender = None
    if with_chatbox:
        if live_audio:
            raise ValueError(
                "--with-chatbox is offline-only; live chatbox goes through the controller process"
            )
        from vrc_agent.control.core import Controller
        from vrc_agent.dialogue.chatbox import ControllerChatSender, SteppedControllerClock
        from vrc_agent.transport.osc import RecordingTransport

        stepped = SteppedControllerClock(None, clock.now_ns())
        controller = Controller(
            "dialogue-offline",
            config.control,
            RecordingTransport(),
            stepped,
        )
        controller.arm(True)
        # 分步推进虚拟时间：让控制层的心跳在「等待节流窗口」期间继续跳。
        stepped.controller = controller
        clock = stepped
        chat_sender = ControllerChatSender(controller, clock=clock)
        notes.append("chatbox: submitted through the real control core with a recording transport")
        notes.append(
            f"chatbox lease is {config.control.lease_ms} ms of virtual time; a replay long "
            "enough to cross it is refused with REJECTED:EXPIRED_LEASE (control layer working "
            "as designed; a real session needs a fresh local ARM)"
        )

    if live_audio:
        backend = SoundDeviceBackend(hostapi_preference=("Windows WASAPI", "MME"))
        notes.append("live audio: real devices are opened; no audio is written to disk")
    else:
        backend = FakeBackend(
            clock=clock,
            capture_tone_hz=dialogue.echo_tone_hz if scenario == "echo" else None,
        )
        notes.append("offline: fake backend, no device is opened and no sound is played")
    devices = backend.list_devices()
    output = select_device(
        devices,
        output_device or dialogue.output_device,
        direction="output",
        hostapi=dialogue.output_hostapi,
        required_channels=chans,
    )
    source = select_device(
        devices,
        input_device or dialogue.input_device,
        direction="input",
        hostapi=dialogue.input_hostapi,
        required_channels=dialogue.capture_channels,
    )

    stop_requested = None
    stop_signal = None
    if live_audio:
        from vrc_agent.control.platform import StopSignal

        stop_signal = StopSignal(state_dir)
        stop_requested = stop_signal.triggered
        notes.append(f"local stop gate: state directory {state_dir}")

    chat_sender = chat_sender  # 已在上面装配（分步时钟与后端共用同一实例）

    session = DialogueSession(
        backend,
        clock=clock,
        queue=ChatQueue(
            session_id="dialogue",
            clock=clock,
            max_pending=dialogue.max_pending,
            ttl_ms=dialogue.queue_ttl_ms,
            min_interval_ms=dialogue.min_interval_ms,
        ),
        half_duplex=HalfDuplex(clock=clock, gate_while_speaking=dialogue.gate_while_speaking),
        tts=FakeTts(),
        asr=(
            FakeAsr(script=tuple(f"user line {index + 1}" for index in range(planned_turns)))
            if dialogue.asr == "fake"
            else QuietAsr(threshold=dialogue.vad_threshold)
        ),
        output_device=output,
        input_device=source,
        sample_rate=rate,
        channels=chans,
        block_ms=block,
        max_utterance_ms=dialogue.max_utterance_ms,
        mode="live" if live_audio else "offline",
        stop_requested=stop_requested,
        chat_sender=chat_sender,
        sink=sink,
    )

    report = None
    echo = None
    extra: dict = {}
    try:
        if scenario == "turns":
            report = session.run_conversation(
                turns=planned_turns,
                replies=tuple(f"reply {index + 1}" for index in range(planned_turns)),
                listen_ms=dialogue.listen_ms,
                use_chatbox=chat_sender is not None,
                use_tts=True,
            )
        elif scenario == "cancel":
            text = "cancel probe: this sentence must not be played to the end"
            if live_audio:
                timer = threading.Timer(0.1, session.stop)
                timer.start()
                session.say(text, index=0, use_chatbox=False, use_tts=True)
                timer.cancel()
            else:
                deadline = clock.now_ns() + 100 * 1_000_000

                def should_stop_now() -> bool:
                    return clock.now_ns() >= deadline

                session.stop_requested = should_stop_now
                session.say(text, index=0, use_chatbox=False, use_tts=True)
                session.stop_requested = None
            report = session.report(turn_count=1)
            extra["cancelled_after_ms"] = 100
        elif scenario == "pause":
            for index in range(5):
                session.queue.enqueue(f"queued before pause {index}")
            extra["pending_before_pause"] = session.queue.stats()["pending"]
            extra["dropped_on_pause"] = session.pause("OPERATOR_PAUSE")
            extra["pending_after_pause"] = session.queue.stats()["pending"]
            session.resume()
            # 这个场景要看的就是队列本身：即使没有配置传输，也让「恢复后那一条」走队列。
            session.say("after resume", index=0, use_chatbox=True, use_tts=True)
            if hasattr(clock, "advance_ms"):
                # 先入队再让时间越过生存期：到点即弃，不补播。
                session.queue.enqueue("stale item that must expire")
                clock.advance_ms(dialogue.queue_ttl_ms + 1_000)
                expired_before = session.queue.dropped_expired
                session.queue.take()
                extra["expired_dropped"] = session.queue.dropped_expired - expired_before
            report = session.report(turn_count=1)
        elif scenario == "echo":
            echo = run_echo_check(
                backend,
                output_device=output,
                input_device=source,
                sample_rate=rate,
                channels=chans,
                probe_ms=dialogue.echo_probe_ms,
                tone_hz=dialogue.echo_tone_hz,
                amplitude=dialogue.echo_amplitude,
                min_ratio=dialogue.echo_min_ratio,
                min_rms=dialogue.echo_min_rms,
                block_ms=block,
                cancel=lambda: bool(session.stop_requested and session.stop_requested()),
            )
            if not live_audio:
                extra["loopback_simulated"] = True
                notes.append("offline echo check: the fake capture returns the probe tone itself")
        else:
            raise ValueError(f"unknown dialogue scenario: {scenario!r}")
    finally:
        if stop_signal is not None:
            stop_signal.close()

    return {
        "mode": "dialogue-live" if live_audio else "dialogue-offline",
        "scenario": scenario,
        "saves_media": False,
        "sends_input": False,
        "live_audio": live_audio,
        "audio_backend": backend.name,
        "devices": {
            "output": output.name,
            "output_index": output.index,
            "output_hostapi": output.hostapi,
            "input": source.name,
            "input_index": source.index,
            "input_hostapi": source.hostapi,
            "virtual_mic": virtual_mic_hint(devices) if live_audio else None,
        },
        "conversation": report.model_dump(mode="json") if report is not None else None,
        "echo": echo.model_dump(mode="json") if echo is not None else None,
        "details": extra,
        "notes": notes,
        "media_policy": "no audio, transcript or chat text is written to disk",
    }


def report_run(
    config: AppConfig,
    *,
    scenario: str = "navigation",
    turns: int = 3,
    fixture_path: Path | None = None,
    max_steps: int = 3,
    session_id: str | None = None,
    log_path: Path | None = None,
    state_dir: Path = Path(".runtime"),
) -> dict:
    """T10：一份离线报告贯穿采集 → 感知 → 规划/动作 → 交流。

    全程离线（回放素材、假供应商、假音频后端）：不发网络请求、不写媒体、不发 OSC、不开
    音频设备。报告里的延迟来自本进程计时，事件与错误码来自结构化日志；没有数据的字段是
    `None` 加原因，不是 0。
    """
    from vrc_agent.capture import (
        ReplaySource,
        collect,
        default_fixture,
        load_fixture,
        parse_fixture,
    )
    from vrc_agent.clock import FakeClock
    from vrc_agent.perception import Perception
    from vrc_agent.providers.factory import build_provider
    from vrc_agent.simulation import run_scenario
    from vrc_agent.telemetry import EventLog
    from vrc_agent.telemetry.budget import SessionBudget
    from vrc_agent.telemetry.codes import codes_of
    from vrc_agent.telemetry.report import SessionRecorder

    fixture = (
        parse_fixture(default_fixture()) if fixture_path is None else load_fixture(fixture_path)
    )
    identity = session_id or new_id()
    path = Path(log_path) if log_path is not None else Path(state_dir) / f"{identity}.jsonl"
    log = EventLog(path, identity)
    budget = SessionBudget.from_config(config.perception)
    recorder = SessionRecorder(session_id=identity, mode="offline", budget=budget, log_path=path)
    log.emit("session_start", mode="offline", operation="report")

    # 1) 采集回放：同一份素材，确定性可复现。
    with recorder.stage("capture_replay"):
        source = ReplaySource(fixture)
        try:
            records = collect(source)
        finally:
            source.close()
    observations = [observation for observation, _ in records]
    frames = {observation.observation_id: frame for observation, frame in records}
    log.emit("capture_replay", operation="replay", count=len(records), status="COMPLETED")

    # 2) 感知：假供应商、逐步计时；看不清就是未知场景，不算错误码。
    clock = FakeClock(fixture.start_ns)
    provider = build_provider(config.perception, clock=clock, accounting=budget)
    perception = Perception(provider, clock=clock)
    scenes = 0
    for observation in observations[:max_steps]:
        started = time.monotonic_ns()
        try:
            scene = asyncio.run(
                perception.interpret(observation, frame=frames.get(observation.observation_id))
            )
        except Exception as exc:
            recorder.stage_ms(
                "perception",
                (time.monotonic_ns() - started) // 1_000_000,
                ok=False,
                code=type(exc).__name__,
            )
            log.emit(
                "perception_failed",
                operation="perceive",
                status="FAILED",
                reason_code=type(exc).__name__,
            )
            continue
        recorder.stage_ms("perception", (time.monotonic_ns() - started) // 1_000_000, ok=True)
        scenes += 1
        recorder.count("scenes", 1)
        if scene.confidence is None:
            recorder.count("unknown_scenes", 1)
        log.emit(
            "perception_scene",
            operation="perceive",
            status="COMPLETED",
            count=len(scene.entities),
        )

    # 3) 规划 + 动作 + 验证：T06 的确定性场景，动作延迟取证据里的时间戳。
    with recorder.stage("planning"):
        runtime_view = asyncio.run(run_scenario(scenario))
    outcome = runtime_view.get("outcome")
    recorder.note_code(outcome if isinstance(outcome, str) else None)
    recorder.note_codes(codes_of(runtime_view))
    recorder.count("actions", len(runtime_view.get("actions") or ()))
    log.emit(
        "runtime_outcome",
        operation="runtime",
        status="COMPLETED",
        reason_code=outcome if isinstance(outcome, str) else None,
        count=len(runtime_view.get("actions") or ()),
    )
    for result in runtime_view.get("results") or ():
        started_ns = result.get("started_at_ns")
        finished_ns = result.get("finished_at_ns")
        if (
            isinstance(started_ns, int)
            and isinstance(finished_ns, int)
            and finished_ns >= started_ns
        ):
            recorder.stage_ms(
                "actuation",
                (finished_ns - started_ns) // 1_000_000,
                ok=result.get("status") == "COMPLETED",
                code=result.get("reason_code"),
            )

    # 4) 交流：离线假后端，不打开设备。
    with recorder.stage("dialogue"):
        dialogue_view = dialogue_run(config, scenario="turns", turns=turns, state_dir=state_dir)
    conversation = dialogue_view.get("conversation") or {}
    dialogue_records = len(conversation.get("records") or ())
    recorder.count("dialogue_records", dialogue_records)
    recorder.note_codes(codes_of(conversation))
    log.emit("dialogue_replay", operation="dialogue", status="COMPLETED", count=dialogue_records)
    log.emit("session_end", operation="report", status="COMPLETED")
    log.close()

    report = recorder.build(
        extra={
            "offline": True,
            "sends_live_input": False,
            "writes_media": False,
            "scenario": scenario,
            "fixture": fixture.name,
            "capture": {
                "source": "replay",
                "observations": len(observations),
                "observations_with_frame": sum(1 for _, frame in records if frame is not None),
            },
            "perception": {
                "provider": config.perception.provider,
                "steps": min(max_steps, len(observations)),
                "scenes": scenes,
            },
            "runtime": {
                "outcome": outcome,
                "verification": runtime_view.get("verification"),
                "final_state": runtime_view.get("final_state"),
                "actions": len(runtime_view.get("actions") or ()),
                "sequence_digest": runtime_view.get("sequence_digest"),
            },
            "dialogue": {
                "turns": conversation.get("turn_count"),
                "records": dialogue_records,
                "total_ms": conversation.get("total_ms"),
                "audio": conversation.get("audio"),
                "chat": conversation.get("chat"),
                "half_duplex": conversation.get("half_duplex"),
            },
            "note": (
                "one offline report spans capture, perception, planning/actuation and dialogue; "
                "no network request, no media file, no OSC output"
            ),
        }
    )
    report["event_log"]["writer"] = log.stats()
    return report


def logs_run(*, state_dir: Path, keep_days: int, apply: bool) -> dict:
    """T10 日志保留：默认只列候选，`--apply` 才真的删除。"""
    from vrc_agent.telemetry.retention import prune_logs

    return prune_logs(state_dir, keep_days=keep_days, apply=apply)


def describe_dialogue(config: AppConfig) -> dict:
    """doctor 用的只读视图：不打开设备、不播放声音、不枚举设备表。"""
    dialogue = config.dialogue
    available = True
    detail = "sounddevice available"
    try:
        import sounddevice  # noqa: F401, PLC0415

        version = sounddevice.__version__
        detail = f"sounddevice {version}"
    except Exception as error:  # pragma: no cover - 缺依赖时触发
        available = False
        detail = f"sounddevice unavailable: {type(error).__name__}"
    return {
        "enabled": dialogue.enabled,
        "output_device": dialogue.output_device,
        "input_device": dialogue.input_device,
        "sample_rate": dialogue.sample_rate,
        "block_ms": dialogue.block_ms,
        "max_pending": dialogue.max_pending,
        "queue_ttl_ms": dialogue.queue_ttl_ms,
        "min_interval_ms": dialogue.min_interval_ms,
        "gate_while_speaking": dialogue.gate_while_speaking,
        "max_utterance_ms": dialogue.max_utterance_ms,
        "audio_backend_available": available,
        "detail": detail,
        "saves_media": False,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="vrc-agent", description="VRChat local control foundations")
    root.add_argument("--version", action="version", version=__version__)
    commands = root.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="validate configuration without sending input")
    doctor.add_argument("--config", type=Path)
    run = commands.add_parser("run", help="default: offline fake-provider demo")
    run.add_argument("--config", type=Path)
    run.add_argument("--mode", choices=["dry-run", "live"])
    run.add_argument("--state-dir", type=Path, default=Path(".runtime"))
    run.add_argument("--interactive", action="store_true", help="dry-run console")
    stop = commands.add_parser("stop", help="request independent emergency stop, not ARM")
    stop.add_argument("--state-dir", type=Path, default=Path(".runtime"))
    capture = commands.add_parser(
        "capture", help="read-only live window capture smoke test (no media is written)"
    )
    capture.add_argument("--config", type=Path)
    capture.add_argument("--frames", type=int, default=3, help="how many observations to take")
    capture.add_argument("--interval-ms", type=int, default=200, help="delay between observations")
    capture.add_argument(
        "--epoch",
        type=int,
        default=1,
        help="environment_epoch to stamp: an armed controller reports 1",
    )
    capture.add_argument("--session-id", default=None, help="default: a fresh random id")
    replay = commands.add_parser("replay", help="replay the deterministic synthetic fixture")
    replay.add_argument("--fixture", type=Path, help="default: the built-in T04 fixture")
    from vrc_agent.simulation import SCENARIOS

    simulate = commands.add_parser(
        "simulate", help="T06 offline perception/action/verification replay"
    )
    simulate.add_argument("--scenario", choices=SCENARIOS, default="navigation")
    commands.add_parser("navigate-suite", help="T08 offline-only fixed 20-task navigation suite")
    navigate = commands.add_parser(
        "navigate-live", help="calibrated navigation; default: plan only"
    )
    navigate.add_argument("--task", required=True)
    navigate.add_argument("--mode", choices=["dry-run", "live"], default="dry-run")
    navigate.add_argument("--config", type=Path)
    navigate.add_argument("--state-dir", type=Path, default=Path(".runtime"))
    navigate.add_argument(
        "--confirm-calibration",
        action="store_true",
        help="confirm private Home, avatar, client, resolution, task pose and safety zones",
    )
    navigate.add_argument("--allow-paid", action="store_true")
    navigate.add_argument("--send-images", action="store_true")
    summary = commands.add_parser(
        "navigation-report", help="summarize fixed live 20-task reports; missing/duplicates fail"
    )
    summary.add_argument("--report-dir", type=Path, default=Path(".runtime/navigation-live"))
    perceive = commands.add_parser(
        "perceive", help="offline perception smoke test: observations -> constrained scenes"
    )
    perceive.add_argument("--config", type=Path)
    perceive.add_argument("--fixture", type=Path, help="default: the built-in T04 fixture")
    perceive.add_argument("--max-steps", type=int, default=5, help="how many observations to read")
    perceive.add_argument(
        "--provider",
        choices=["fake", "openai_compat"],
        default=None,
        help="override [perception].provider; openai_compat needs --allow-paid",
    )
    perceive.add_argument(
        "--allow-paid",
        action="store_true",
        help="confirm that a paid provider may be called over the network",
    )
    perceive.add_argument(
        "--send-images",
        action="store_true",
        help="also send the in-memory frame to the provider (paid providers only)",
    )
    dialogue = commands.add_parser(
        "dialogue", help="T07 offline dialogue smoke test: queue, half-duplex, TTS, echo check"
    )
    dialogue.add_argument("--config", type=Path)
    dialogue.add_argument(
        "--devices", action="store_true", help="list audio devices read-only and exit"
    )
    dialogue.add_argument(
        "--scenario", choices=["turns", "cancel", "pause", "echo"], default="turns"
    )
    dialogue.add_argument("--turns", type=int, default=None, help="how many turns to replay")
    dialogue.add_argument(
        "--live-audio",
        action="store_true",
        help="open real audio devices and play sound; without it everything stays offline",
    )
    dialogue.add_argument(
        "--with-chatbox",
        action="store_true",
        help="also submit replies as chatbox actions through the offline control core",
    )
    dialogue.add_argument(
        "--output-device", default=None, help="device name substring to play into"
    )
    dialogue.add_argument(
        "--input-device", default=None, help="device name substring to record from"
    )
    dialogue.add_argument("--sample-rate", type=int, default=None)
    dialogue.add_argument("--channels", type=int, default=None)
    dialogue.add_argument("--block-ms", type=int, default=None)
    dialogue.add_argument("--state-dir", type=Path, default=Path(".runtime"))

    report = commands.add_parser(
        "report", help="T10 offline session report: events, codes, latency, usage and budget"
    )
    report.add_argument("--config", type=Path)
    report.add_argument("--scenario", choices=list(SCENARIOS), default="navigation")
    report.add_argument("--turns", type=int, default=3, help="dialogue turns to replay")
    report.add_argument("--fixture", type=Path, help="capture fixture; default the packaged replay")
    report.add_argument("--max-steps", type=int, default=3, help="perception steps to measure")
    report.add_argument(
        "--log", type=Path, help="event log path; default <state-dir>/<session id>.jsonl"
    )
    report.add_argument("--state-dir", type=Path, default=Path(".runtime"))

    logs = commands.add_parser(
        "logs", help="T10 structured log retention: list candidates, delete only with --apply"
    )
    from vrc_agent.telemetry.retention import DEFAULT_KEEP_DAYS

    logs.add_argument("--state-dir", type=Path, default=Path(".runtime"))
    logs.add_argument("--keep-days", type=int, default=DEFAULT_KEEP_DAYS)
    logs.add_argument("--apply", action="store_true", help="actually delete the listed log files")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "stop":
            signal_stop(args.state_dir)
            print("Emergency stop signalled; delivery does not prove the avatar has stopped.")
            return 0
        if args.command == "replay":
            print_json(replay_run(args.fixture))
            return 0
        if args.command == "simulate":
            from vrc_agent.simulation import run_scenario

            print_json(asyncio.run(run_scenario(args.scenario)))
            return 0  # 回放成功运行不等于导航成功；任务结果见 outcome/verification。
        if args.command == "navigate-suite":
            from vrc_agent.navigation_sim import run_navigation_suite

            print_json(asyncio.run(run_navigation_suite()))
            return 0  # 正常完成试验不代表导航全成功；查看 success_rate/failure_counts。
        if args.command == "navigation-report":
            from vrc_agent.live_navigation.session import summarize_reports

            print_json(summarize_reports(args.report_dir))
            return 0
        if args.command == "navigate-live":
            from vrc_agent.live_navigation.session import navigation_entry

            print_json(asyncio.run(navigation_entry(args)))
            return 0
        if args.command == "logs":
            if args.keep_days < 0:
                raise ValueError("--keep-days must be >= 0")
            print_json(
                logs_run(state_dir=args.state_dir, keep_days=args.keep_days, apply=args.apply)
            )
            return 0
        config = load_config(args.config)
        if args.command == "doctor":
            print_json(
                {
                    "version": __version__,
                    "python": sys.version.split()[0],
                    "config_valid": True,
                    "default_mode": config.mode,
                    "provider": config.provider,
                    "live_platform_available": os.name == "nt",
                    "client_capabilities_verified": False,
                    "dpi_awareness": capture_awareness(),
                    "capture_window_title": config.capture_window_title(),
                    "capture_requires_unoccluded": config.capture.require_unoccluded,
                    "perception": describe_provider(config.perception),
                    "dialogue": describe_dialogue(config),
                    "note": (
                        "No sockets opened; ports, hotkeys and VRChat are not verified. "
                        "The capture and perception fields are read-only observations of this "
                        "process and config; no model request is sent and no key is printed."
                    ),
                }
            )
            return 0
        if args.command == "perceive":
            if not 1 <= args.max_steps <= 100:
                raise ValueError("--max-steps must be between 1 and 100")
            print_json(
                perceive_run(
                    config,
                    fixture_path=args.fixture,
                    max_steps=args.max_steps,
                    provider_name=args.provider,
                    allow_paid=args.allow_paid,
                    send_images=args.send_images,
                )
            )
            return 0
        if args.command == "dialogue":
            if args.turns is not None and not 1 <= args.turns <= 200:
                raise ValueError("--turns must be between 1 and 200")
            print_json(
                dialogue_run(
                    config,
                    scenario="devices" if args.devices else args.scenario,
                    turns=args.turns,
                    live_audio=args.live_audio,
                    with_chatbox=args.with_chatbox,
                    output_device=args.output_device,
                    input_device=args.input_device,
                    sample_rate=args.sample_rate,
                    channels=args.channels,
                    block_ms=args.block_ms,
                    state_dir=args.state_dir,
                )
            )
            return 0
        if args.command == "report":
            if not 1 <= args.turns <= 200:
                raise ValueError("--turns must be between 1 and 200")
            if not 1 <= args.max_steps <= 100:
                raise ValueError("--max-steps must be between 1 and 100")
            print_json(
                report_run(
                    config,
                    scenario=args.scenario,
                    turns=args.turns,
                    fixture_path=args.fixture,
                    max_steps=args.max_steps,
                    log_path=args.log,
                    state_dir=args.state_dir,
                )
            )
            return 0
        if args.command == "capture":
            if not 1 <= args.frames <= 100:
                raise ValueError("--frames must be between 1 and 100")
            if not 0 <= args.interval_ms <= 10000:
                raise ValueError("--interval-ms must be between 0 and 10000")
            if args.epoch < 0:
                raise ValueError("--epoch must be >= 0")
            print_json(
                capture_smoke(
                    config,
                    frames=args.frames,
                    interval_ms=args.interval_ms,
                    epoch=args.epoch,
                    session_id=args.session_id or new_id(),
                )
            )
            return 0
        if args.mode:
            config = AppConfig.model_validate({**config.model_dump(), "mode": args.mode})
        if config.mode == "live" and args.interactive:
            raise ValueError("live uses local hotkeys, not the dry-run interactive console")
        with ControllerProcess(config, args.state_dir) as client:
            if config.mode == "dry-run":
                if args.interactive:
                    interactive(client)
                else:
                    result = demo(client)
                    print_json(result)
                    if result["result"]["status"] != "COMPLETED":
                        return 1
            else:
                print(
                    "LIVE starts DISARMED. Focus configured VRChat window. F9=ARM (30s default), "
                    "F8=ESTOP, F10=RESET. Ctrl+C exits. No autonomous planner is installed.",
                    flush=True,
                )
                if config.control.enable_motion_probe:
                    print(
                        "Manual probes enabled while armed: F7=forward, Ctrl+F11=backward, "
                        "F5=strafe left, F4=strafe right (200ms pulses); F6=yaw right, "
                        "Ctrl+F12=yaw left (500ms max-strength); F3=use (UseRight pulse); "
                        "F1=grab (GrabRight pulse); F11=drop (DropRight pulse); "
                        "F2=chatbox message.",
                        flush=True,
                    )
                previous = None
                while True:
                    status = client.request("status")
                    summary = (status["state"], status["reason_code"])
                    if summary != previous:
                        print_json({"state": summary[0], "reason_code": summary[1]})
                        previous = summary
                    time.sleep(0.2)
        return 0
    except (KeyboardInterrupt, EOFError):
        print("Stopped.")
        return 0
    except UnicodeError:
        print("Output encoding error; no configuration diagnosis was made.", file=sys.stderr)
        return 1
    except ValidationError:
        # pydantic 的报错会回显输入值，里面可能夹带配置里的敏感内容，这里只给固定文案。
        print(
            "Invalid configuration or command. Check types, bounds and controller state.",
            file=sys.stderr,
        )
        return 2
    except ValueError as exc:
        # 我们自己的边界检查（如 `--turns` 范围）用的是固定文案，不含用户数据，可以照实说。
        print(f"Invalid command: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, TimeoutError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
