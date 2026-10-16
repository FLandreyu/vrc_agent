"""半双工状态机：播放时门控监听、暂停不监听、急停后必须人工重置。"""

from __future__ import annotations

import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.dialogue import DialogueError, HalfDuplex


def make(*, gate: bool = True) -> tuple[HalfDuplex, FakeClock]:
    clock = FakeClock(1_000_000_000)
    return HalfDuplex(clock=clock, gate_while_speaking=gate), clock


def test_starts_idle_and_only_listening_accepts_capture() -> None:
    half, _ = make()
    assert half.state == "IDLE"
    assert half.can_listen is False
    assert half.capture_gated is True
    half.begin_listening()
    assert half.can_listen is True
    assert half.capture_gated is False


def test_speaking_gates_the_microphone_and_finishing_restores_it() -> None:
    half, _ = make()
    half.begin_listening()
    half.begin_speaking()
    assert half.state == "SPEAKING"
    assert half.can_listen is False  # 首版不做回声消除：播放期间一律门控
    half.note_gated_capture(5)
    assert half.stats()["gated_captures"] == 5
    half.finish_speaking()
    assert half.can_listen is True
    assert half.stats()["capture_blocks"] == 0


def test_transitions_are_recorded_with_reason_codes() -> None:
    half, clock = make()
    half.begin_listening()
    clock.advance_ms(10)
    half.begin_speaking()
    clock.advance_ms(10)
    half.pause("OPERATOR_PAUSE")
    half.resume()
    half.stop("MANUAL_STOP")
    states = [(t.previous, t.state, t.reason) for t in half.transitions]
    assert states == [
        ("IDLE", "LISTENING", "user_speech_window"),
        ("LISTENING", "SPEAKING", "ai_speaking"),
        ("SPEAKING", "PAUSED", "OPERATOR_PAUSE"),
        ("PAUSED", "LISTENING", "resumed"),
        ("LISTENING", "STOPPED", "MANUAL_STOP"),
    ]
    assert half.transitions[1].at_ns == 1_010_000_000


def test_stopped_is_terminal_until_an_explicit_reset() -> None:
    half, _ = make()
    half.begin_listening()
    half.stop("MANUAL_STOP")
    for action in (half.begin_listening, half.begin_speaking, half.resume, half.pause):
        with pytest.raises(DialogueError) as info:
            action()
        assert info.value.failure == "PAUSED"
    half.reset()
    assert half.state == "IDLE"
    half.begin_listening()
    assert half.can_listen is True


def test_illegal_transitions_are_rejected_not_ignored() -> None:
    half, _ = make()
    with pytest.raises(DialogueError):
        half.resume()  # IDLE 不能 resume
    with pytest.raises(DialogueError):
        half.finish_speaking()  # 没在说话
    assert half.state == "IDLE"


def test_transition_history_is_bounded() -> None:
    half, _ = make()
    half.max_transitions = 3
    half.begin_listening()
    for _ in range(5):
        half.begin_speaking()
        half.finish_speaking()
    assert len(half.transitions) == 3
    assert half.stats()["transitions"] == 3
    # 只保留最近的三条：最后两条一定是 speaking/finished
    assert [t.state for t in half.transitions][-2:] == ["SPEAKING", "LISTENING"]


def test_gate_while_speaking_can_be_disabled_but_defaults_on() -> None:
    half, _ = make(gate=False)
    half.begin_listening()
    half.begin_speaking()
    assert half.gate_while_speaking is False
    assert half.stats()["gate_while_speaking"] == 0
