"""音频后端与自声回流诊断：全部离线，不打开任何真实设备。"""

from __future__ import annotations

import pytest

from vrc_agent.clock import FakeClock
from vrc_agent.dialogue import (
    DialogueError,
    FakeBackend,
    dsp,
    run_echo_check,
)
from vrc_agent.dialogue.echo import analyse

RATE = 48_000


def make_backend(**changes) -> FakeBackend:
    options = {"clock": FakeClock(1_000_000_000)}
    options.update(changes)
    return FakeBackend(**options)


def output_device(backend: FakeBackend):
    return next(d for d in backend.list_devices() if d.supports("output") and "Speakers" in d.name)


def input_device(backend: FakeBackend):
    return next(d for d in backend.list_devices() if d.supports("input"))


# ------------------------------------------------------------------ 后端


def test_full_playback_reports_the_requested_duration_block_by_block() -> None:
    backend = make_backend()
    tone = dsp.sine_tone(440.0, 200, RATE)
    result = backend.play(
        tone, sample_rate=RATE, channels=1, device=output_device(backend), block_ms=20
    )
    assert result.cancelled is False
    assert result.requested_ms == 200 and result.played_ms == 200
    assert result.blocks == 10  # 200ms / 20ms
    assert result.completed is True
    assert result.sample_hash == dsp.hash_prefix(tone)
    assert backend.opened == [f"out:{output_device(backend).name}"]


def test_cancelling_playback_stops_within_one_block() -> None:
    backend = make_backend()
    clock = backend.clock
    tone = dsp.sine_tone(440.0, 5_000, RATE)
    deadline = clock.now_ns() + 100 * 1_000_000  # 100 ms 后「本机停止」
    result = backend.play(
        tone,
        sample_rate=RATE,
        channels=1,
        device=output_device(backend),
        cancel=lambda: clock.now_ns() >= deadline,
        block_ms=20,
    )
    assert result.cancelled is True
    assert result.played_ms == 100  # 一块 20ms，误差不超过一块
    assert result.requested_ms == 5_000
    assert result.completed is False


def test_recording_honours_duration_channels_and_cancel() -> None:
    backend = make_backend()
    device = input_device(backend)
    capture = backend.record(
        sample_rate=RATE, channels=1, device=device, duration_ms=100, block_ms=20
    )
    assert capture.duration_ms == 100
    assert len(capture.samples) == 4_800
    assert capture.cancelled is False
    capture.discard()
    assert len(capture.samples) == 0  # 显式丢弃：默认不保存真人语音

    short = backend.record(
        sample_rate=RATE,
        channels=1,
        device=device,
        duration_ms=1_000,
        cancel=lambda: True,
        block_ms=20,
    )
    assert short.cancelled is True and short.duration_ms == 0
    assert len(backend.capture_log) == 2


def test_capture_can_simulate_a_loopback_tone() -> None:
    backend = make_backend(capture_tone_hz=880.0, capture_amplitude=0.3)
    capture = backend.record(
        sample_rate=RATE, channels=1, device=input_device(backend), duration_ms=200, block_ms=20
    )
    assert dsp.tone_ratio(capture.samples, 880.0, RATE) > 0.9


def test_wrong_direction_and_injected_failures_are_explicit() -> None:
    backend = make_backend()
    speakers = output_device(backend)
    with pytest.raises(DialogueError) as info:
        backend.record(sample_rate=RATE, channels=1, device=speakers, duration_ms=50)
    assert info.value.failure == "DEVICE_UNAVAILABLE"

    failing = make_backend(fail_play=True, fail_record=True)
    with pytest.raises(DialogueError) as info:
        failing.play(
            dsp.sine_tone(440.0, 50, RATE),
            sample_rate=RATE,
            channels=1,
            device=output_device(failing),
        )
    assert info.value.failure == "STREAM_UNAVAILABLE"
    with pytest.raises(DialogueError):
        failing.record(sample_rate=RATE, channels=1, device=input_device(failing), duration_ms=50)


def test_fake_device_table_covers_the_virtual_mic_route() -> None:
    backend = make_backend()
    names = [device.name for device in backend.list_devices()]
    assert any(name.startswith("Voicemeeter Input") for name in names)
    assert any(name.startswith("Voicemeeter Out B1") for name in names)
    in_index, out_index = backend.default_device_indices()
    assert in_index == 0 and out_index == 1
    with pytest.raises(DialogueError):
        backend.resolve(99, "output")


# ------------------------------------------------------------------ 回流判定


def test_analyse_classifies_loopback_silence_and_wrong_tone() -> None:
    tone = dsp.sine_tone(880.0, 200, RATE, amplitude=0.3)
    quiet = dsp.silence(200, RATE)
    other = dsp.sine_tone(440.0, 200, RATE, amplitude=0.3)
    assert (
        analyse(tone, sample_rate=RATE, channels=1, tone_hz=880.0, min_ratio=0.25, min_rms=0.002)[3]
        == "loopback_detected"
    )
    assert (
        analyse(quiet, sample_rate=RATE, channels=1, tone_hz=880.0, min_ratio=0.25, min_rms=0.002)[
            3
        ]
        == "no_signal"
    )
    assert (
        analyse(other, sample_rate=RATE, channels=1, tone_hz=880.0, min_ratio=0.25, min_rms=0.002)[
            3
        ]
        == "no_loopback"
    )


def test_echo_check_detects_a_simulated_loopback_and_discards_the_capture() -> None:
    backend = make_backend(capture_tone_hz=880.0, capture_amplitude=0.3)
    report = run_echo_check(
        backend,
        output_device=output_device(backend),
        input_device=input_device(backend),
        sample_rate=RATE,
        probe_ms=200,
        tone_hz=880.0,
        min_ratio=0.25,
        min_rms=0.002,
    )
    assert report.verdict == "loopback_detected"
    assert report.played_ms > 0 and report.recorded_ms > 0
    assert report.tone_ratio > 0.5
    assert len(backend.capture_log[-1].samples) == 0  # 判定完立刻丢弃录音缓冲


def test_echo_check_reports_no_signal_and_stream_errors() -> None:
    silent = make_backend()
    report = run_echo_check(
        silent,
        output_device=output_device(silent),
        input_device=input_device(silent),
        sample_rate=RATE,
        probe_ms=100,
    )
    assert report.verdict == "no_signal"
    assert report.rms == 0.0

    broken = make_backend(fail_play=True)
    report = run_echo_check(
        broken,
        output_device=output_device(broken),
        input_device=input_device(broken),
        sample_rate=RATE,
        probe_ms=100,
    )
    assert report.verdict == "stream_error"
    assert any("STREAM_UNAVAILABLE" in note for note in report.notes)


def test_echo_check_cancel_is_recorded() -> None:
    backend = make_backend(capture_tone_hz=880.0)
    report = run_echo_check(
        backend,
        output_device=output_device(backend),
        input_device=input_device(backend),
        sample_rate=RATE,
        probe_ms=200,
        cancel=lambda: True,
    )
    assert report.verdict in {"no_signal", "stream_error", "loopback_detected"}
    assert any("cancelled" in note for note in report.notes)


def test_echo_report_carries_no_audio() -> None:
    backend = make_backend(capture_tone_hz=880.0)
    report = run_echo_check(
        backend,
        output_device=output_device(backend),
        input_device=input_device(backend),
        sample_rate=RATE,
        probe_ms=100,
    )
    payload = report.model_dump(mode="json")
    assert "samples" not in payload
    assert set(payload) >= {"verdict", "tone_ratio", "rms", "played_ms", "recorded_ms"}
