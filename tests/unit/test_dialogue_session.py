"""对话会话：计时记录、播放可被立即取消、暂停不积压、门控与隐私。"""

from __future__ import annotations

import json

import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.config import ControlConfig
from vrc_agent.control.core import Controller
from vrc_agent.dialogue import (
    ChatQueue,
    DialogueError,
    DialogueSession,
    FakeAsr,
    FakeBackend,
    FakeTts,
    HalfDuplex,
    QuietAsr,
    RecordingChatSender,
)
from vrc_agent.dialogue.chatbox import ControllerChatSender, SteppedControllerClock
from vrc_agent.transport.osc import RecordingTransport

RATE = 48_000
REPLY = "hello there"


def make_session(**changes) -> tuple[DialogueSession, FakeBackend, FakeClock]:
    clock = FakeClock(1_000_000_000)
    backend = FakeBackend(clock=clock)
    devices = backend.list_devices()
    output = next(d for d in devices if "Voicemeeter Input" in d.name)
    source = next(d for d in devices if d.supports("input"))
    options = {
        "backend": backend,
        "clock": clock,
        "session_id": "test-session",
        "queue": ChatQueue(session_id="test-session", clock=clock, min_interval_ms=0),
        "half_duplex": HalfDuplex(clock=clock),
        "tts": FakeTts(),
        "asr": FakeAsr(script=tuple(f"user line {index}" for index in range(24))),
        "output_device": output,
        "input_device": source,
        "sample_rate": RATE,
        "channels": 1,
        "block_ms": 20,
    }
    options.update(changes)
    return DialogueSession(**options), backend, clock


# ------------------------------------------------------------- 20 轮计时


def test_twenty_turns_produce_timed_records_for_every_action() -> None:
    session, _, _ = make_session()
    report = session.run_conversation(turns=20, replies=tuple(f"reply {i}" for i in range(20)))

    assert report.turn_count == 20
    assert len(report.records) == 40  # 每轮：一次监听 + 一次播放
    actions = [record.action for record in report.records]
    assert actions == ["listen", "playback"] * 20
    assert {record.status for record in report.records} == {"COMPLETED"}
    assert {record.reason_code for record in report.records} == {"OK"}
    assert all(record.duration_ms > 0 for record in report.records)
    assert all(record.ended_at_ns >= record.started_at_ns for record in report.records)
    assert report.total_ms == sum(record.duration_ms for record in report.records)
    assert report.saves_media is False
    assert report.audio["sample_rate"] == RATE


def test_records_are_ordered_by_time_and_indexed_by_turn() -> None:
    session, _, _ = make_session()
    report = session.run_conversation(turns=3, replies=("a", "b", "c"))
    assert [record.index for record in report.records] == [0, 0, 1, 1, 2, 2]
    timestamps = [record.started_at_ns for record in report.records]
    assert timestamps == sorted(timestamps)


def test_audio_totals_are_summed_from_the_records() -> None:
    session, _, _ = make_session()
    report = session.run_conversation(turns=3, replies=("a", "b", "c"))
    playback = [record for record in report.records if record.action == "playback"]
    listens = [record for record in report.records if record.action == "listen"]
    # 不读后端计数器（假后端根本没有）：报告的合计必须等于逐条记录之和
    assert report.audio["played_ms"] == sum(record.played_ms for record in playback) > 0
    assert report.audio["requested_ms"] == sum(record.requested_ms for record in playback)
    assert report.audio["listen_ms"] == sum(record.duration_ms for record in listens) == 900
    assert report.audio["playback_records"] == 3
    assert report.audio["listen_records"] == 3


# ------------------------------------------------------- 播放可立即取消


def test_playback_is_cancelled_within_one_block_by_the_local_stop() -> None:
    session, _, clock = make_session()
    deadline = clock.now_ns() + 100 * 1_000_000  # 100 ms 后「本机停止」
    session.stop_requested = lambda: clock.now_ns() >= deadline
    records = session.say("a much longer sentence that would take seconds to speak", index=0)
    playback = next(record for record in records if record.action == "playback")
    assert playback.status == "CANCELLED"
    assert playback.reason_code == "CANCELLED"
    assert playback.played_ms == 100
    assert playback.requested_ms > 1_000  # 请求的是"好几秒"，实际只播了 100 ms


def test_stop_clears_pending_speech_and_marks_the_session_stopped() -> None:
    session, _, _ = make_session()
    for index in range(3):
        session.queue.enqueue(f"queued {index}")
    assert session.stop("MANUAL_STOP") == 3
    assert session.queue.stats()["pending"] == 0
    assert session.half_duplex.state == "STOPPED"
    assert session.stats()["stopped"] is True
    with pytest.raises(DialogueError):
        session.resume()  # 急停后必须人工重置


# ---------------------------------------------------- 暂停不积压旧发言


def test_pause_clears_the_queue_and_old_speech_is_never_replayed() -> None:
    session, backend, _ = make_session()
    for index in range(4):
        session.queue.enqueue(f"before pause {index}")
    assert session.pause("OPERATOR_PAUSE") == 4
    assert session.queue.stats()["pending"] == 0
    assert session.dropped_on_pause == 4
    assert session.half_duplex.state == "PAUSED"

    with pytest.raises(DialogueError) as info:
        session.queue.enqueue("while paused")  # 暂停期间明确拒绝，而不是偷偷排队
    assert info.value.failure == "PAUSED"
    session.resume()
    assert session.half_duplex.state == "LISTENING"  # resume 之后回到监听
    records = session.say("after resume", index=0, use_chatbox=True, use_tts=False)
    # 离线没有传输层，所以只记 NO_TRANSPORT；关键点是**只发出恢复之后那一条**。
    assert [record.reason_code for record in records] == ["NO_TRANSPORT"]
    assert session.queue.stats()["sent"] == 1
    assert backend.playback_log == []


def test_paused_queue_refuses_new_speech_explicitly() -> None:
    session, _, _ = make_session()
    session.pause("OPERATOR_PAUSE")
    records = session.say("while paused", index=0, use_chatbox=True, use_tts=False)
    assert records[0].status == "DROPPED"
    assert records[0].reason_code == "PAUSED"


# --------------------------------------------------------------- 门控


def test_listening_is_gated_while_speaking_and_nothing_is_recorded() -> None:
    session, backend, _ = make_session()
    session.half_duplex.begin_listening()
    session.half_duplex.begin_speaking()
    record = session.listen(index=0, duration_ms=300)
    assert record.status == "SKIPPED"
    assert record.reason_code == "MIC_GATED"
    assert backend.capture_log == []  # 播放期间不录音、不转写
    assert session.half_duplex.stats()["gated_captures"] == 15

    session.half_duplex.finish_speaking()
    ok = session.listen(index=0, duration_ms=100)
    assert ok.status == "COMPLETED"
    assert len(backend.capture_log) == 1


def test_transcripts_are_discarded_and_reports_carry_no_text() -> None:
    session, _, _ = make_session()
    session.half_duplex.begin_listening()
    record = session.listen(index=0, duration_ms=100)
    assert record.status == "COMPLETED"
    assert session.last_transcript is not None
    assert record.text_chars == len(session.last_transcript.text) == len("user line 0")
    report = session.report(turn_count=1)
    blob = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert "user line" not in blob
    assert REPLY not in blob


def test_text_is_validated_even_when_the_chatbox_is_not_used() -> None:
    session, _, _ = make_session()
    records = session.say("x" * 200, index=0, use_chatbox=False, use_tts=True)
    assert records[0].status == "DROPPED"
    assert records[0].reason_code == "TEXT_INVALID"


def test_missing_asr_is_reported_instead_of_inventing_speech() -> None:
    session, _, _ = make_session(asr=None)
    session.half_duplex.begin_listening()
    record = session.listen(index=0, duration_ms=50)
    assert record.reason_code == "NO_ASR"
    assert record.text_chars == 0


# -------------------------------------------------- chatbox 与控制层


def test_chatbox_goes_through_the_real_control_core() -> None:
    clock = FakeClock(1_000_000_000)
    controller = Controller("chat-session", ControlConfig(), RecordingTransport(), clock)
    controller.arm(True)
    sender = ControllerChatSender(controller, clock=clock)
    backend = FakeBackend(clock=clock)
    session = DialogueSession(
        backend,
        clock=clock,
        session_id="chat-session",
        queue=ChatQueue(session_id="chat-session", clock=clock, min_interval_ms=0),
        half_duplex=HalfDuplex(clock=clock),
        tts=FakeTts(),
        output_device=next(d for d in backend.list_devices() if "Voicemeeter Input" in d.name),
        input_device=next(d for d in backend.list_devices() if d.supports("input")),
        sample_rate=RATE,
        chat_sender=sender,
    )
    records = session.say(REPLY, index=0, use_tts=False)
    assert records[0].status == "COMPLETED"
    assert records[0].reason_code == "OK"
    assert controller.snapshot()["state"] in {"ARMED_IDLE", "EXECUTING"}


def test_control_layer_rate_limit_is_visible_in_the_session_record() -> None:
    clock = FakeClock(1_000_000_000)
    control = ControlConfig(chat_interval_ms=2_000)
    controller = Controller("chat-session", control, RecordingTransport(), clock)
    controller.arm(True)
    sender = ControllerChatSender(controller, clock=clock)
    backend = FakeBackend(clock=clock)
    session = DialogueSession(
        backend,
        clock=clock,
        session_id="chat-session",
        queue=ChatQueue(session_id="chat-session", clock=clock, min_interval_ms=0),
        half_duplex=HalfDuplex(clock=clock),
        tts=FakeTts(),
        output_device=next(d for d in backend.list_devices() if "Voicemeeter Input" in d.name),
        input_device=next(d for d in backend.list_devices() if d.supports("input")),
        sample_rate=RATE,
        chat_sender=sender,
    )
    first = session.say("first", index=0, use_tts=False)[0]
    second = session.say("second", index=1, use_tts=False)[0]
    assert first.status == "COMPLETED"
    assert second.status == "DROPPED"
    assert second.reason_code == "REJECTED:RATE_LIMITED"


def test_a_stopped_controller_is_reported_not_crashed() -> None:
    clock = FakeClock(1_000_000_000)
    controller = Controller("chat-session", ControlConfig(), RecordingTransport(), clock)
    controller.arm(True)
    sender = ControllerChatSender(controller, clock=clock)
    controller.stop("MANUAL_STOP")
    assert sender("hello") == "REJECTED:MANUAL_STOP"
    assert sender.rejected == 1


def test_stepped_clock_keeps_the_controller_armed_across_long_waits() -> None:
    clock = FakeClock(1_000_000_000)
    stepped = SteppedControllerClock(None, clock.now_ns())
    controller = Controller("chat-session", ControlConfig(), RecordingTransport(), stepped)
    controller.arm(True)
    stepped.controller = controller
    stepped.advance_ms(2_000)  # 只有真的补心跳，控制器才不会因 500ms 超时而停下
    assert controller.snapshot()["state"] == "ARMED_IDLE"


def test_recording_chat_sender_is_the_offline_default() -> None:
    backend = FakeBackend(clock=FakeClock())
    sender = RecordingChatSender()
    session = DialogueSession(
        backend,
        clock=backend.clock,
        session_id="offline",
        queue=ChatQueue(session_id="offline", clock=backend.clock, min_interval_ms=0),
        half_duplex=HalfDuplex(clock=backend.clock),
        tts=FakeTts(),
        output_device=next(d for d in backend.list_devices() if "Voicemeeter Input" in d.name),
        input_device=next(d for d in backend.list_devices() if d.supports("input")),
        sample_rate=RATE,
        chat_sender=sender,
    )
    records = session.say("offline reply", index=0, use_tts=False)
    assert sender.sent == ["offline reply"]
    assert records[0].reason_code == "OK"


def test_a_long_chatbox_replay_eventually_crosses_the_lease_and_is_refused() -> None:
    """租约到期后 chatbox 会被控制层拒绝——这是设计行为，不是丢消息。

    离线 20 轮每轮约 2.3 s 虚拟时间，会越过 `ControlConfig.lease_ms`（默认 30_000 ms）；
    真实会话里到期需要人工重新 ARM（F9）。
    """
    clock = FakeClock(1_000_000_000)
    stepped = SteppedControllerClock(None, clock.now_ns())
    controller = Controller("chat-session", ControlConfig(), RecordingTransport(), stepped)
    controller.arm(True)
    stepped.controller = controller
    backend = FakeBackend(clock=stepped)
    session = DialogueSession(
        backend,
        clock=stepped,
        session_id="chat-session",
        queue=ChatQueue(session_id="chat-session", clock=stepped, min_interval_ms=2_000),
        half_duplex=HalfDuplex(clock=stepped),
        tts=FakeTts(),
        output_device=next(d for d in backend.list_devices() if "Voicemeeter Input" in d.name),
        input_device=next(d for d in backend.list_devices() if d.supports("input")),
        sample_rate=RATE,
        chat_sender=ControllerChatSender(controller, clock=stepped),
    )
    report = session.run_conversation(turns=20, replies=tuple(f"reply {i}" for i in range(20)))
    chatbox = [record for record in report.records if record.action == "chatbox"]
    assert len(chatbox) == 20
    assert chatbox[0].reason_code == "OK"  # 租约内正常
    # 越界之后被明确拒绝；队列统计显示没有任何静默丢弃
    assert any(record.reason_code == "REJECTED:EXPIRED_LEASE" for record in chatbox)
    assert report.chat["dropped_expired"] == 0
    assert report.chat["dropped_full"] == 0


def test_quiet_asr_reports_silence_without_text() -> None:
    session, _, _ = make_session(asr=QuietAsr(threshold=0.05))
    session.half_duplex.begin_listening()
    record = session.listen(index=0, duration_ms=100)
    assert record.status == "COMPLETED"
    assert record.reason_code == "SILENCE"
    assert record.text_chars == 0
    assert session.last_transcript is not None
    assert session.last_transcript.is_speech is False
