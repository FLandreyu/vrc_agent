"""自声回流诊断：放一段已知的探针音，同时从配置的输入录回来，判断有没有回流。

用途：确认「AI 说话」这条路真的通到了目标输入设备（例如 Voicemeeter 的虚拟输入），
以及确认监听设备能不能听到它。判定只看**频率能量占比**与 RMS，不做任何语音识别，
也不需要识别引擎。

顺序：播放放在后台线程，录音在调用线程里同时进行；两边的取消回调都会在**一个音频块**
（默认 20 ms）之内生效。录音缓冲只在内存里，判定完立即丢弃。
"""

from __future__ import annotations

import threading
from typing import Literal

from pydantic import Field

from vrc_agent.contracts import Contract
from vrc_agent.dialogue import dsp
from vrc_agent.dialogue.backend import AudioBackend, Cancel, never_cancelled
from vrc_agent.dialogue.devices import AudioDevice
from vrc_agent.dialogue.errors import DialogueError

EchoVerdict = Literal["loopback_detected", "no_loopback", "no_signal", "stream_error"]

#: 默认探针：880 Hz、300 ms、很小的音量（只在设备上短暂出现，不是给用户听的内容）。
DEFAULT_PROBE_HZ = 880.0
DEFAULT_PROBE_MS = 300
DEFAULT_AMPLITUDE = 0.08


class EchoReport(Contract):
    """一次回流诊断的结果。只有设备名、时长、能量与判定，**没有音频内容**。"""

    output_device: str
    input_device: str
    tone_hz: float
    probe_ms: int
    played_ms: int
    recorded_ms: int
    rms: float
    peak: float
    tone_ratio: float
    min_ratio: float
    min_rms: float
    verdict: EchoVerdict
    notes: tuple[str, ...] = Field(default_factory=tuple)


def analyse(
    samples: "array",  # noqa: F821 - array("f")，只在内存里
    *,
    sample_rate: int,
    channels: int,
    tone_hz: float,
    min_ratio: float,
    min_rms: float,
) -> tuple[float, float, float, EchoVerdict]:
    """把录到的采样判成结论：返回 (rms, peak, tone_ratio, verdict)。"""
    energy = dsp.rms(samples)
    top = dsp.peak(samples)
    ratio = dsp.tone_ratio(samples, tone_hz, sample_rate, channels=channels)
    if energy < min_rms:
        return energy, top, ratio, "no_signal"
    return energy, top, ratio, "loopback_detected" if ratio >= min_ratio else "no_loopback"


def run_echo_check(
    backend: AudioBackend,
    *,
    output_device: AudioDevice,
    input_device: AudioDevice,
    sample_rate: int = 48_000,
    channels: int = 1,
    probe_ms: int = DEFAULT_PROBE_MS,
    tone_hz: float = DEFAULT_PROBE_HZ,
    amplitude: float = DEFAULT_AMPLITUDE,
    min_ratio: float = 0.25,
    min_rms: float = 0.002,
    block_ms: int = 20,
    cancel: Cancel = never_cancelled,
    playback_lead_ms: int | None = None,
) -> EchoReport:
    """播放探针音并同时录音，返回判定。任何设备失败都变成 `stream_error` 报告。"""
    if probe_ms <= 0:
        raise DialogueError("TEXT_INVALID", "probe_ms must be positive")
    lead_ms = 2 * block_ms if playback_lead_ms is None else max(0, playback_lead_ms)
    samples = dsp.sine_tone(tone_hz, probe_ms, sample_rate, channels=channels, amplitude=amplitude)
    failures: list[str] = []
    playback_result: dict[str, int | bool] = {}

    def _play() -> None:
        try:
            result = backend.play(
                samples,
                sample_rate=sample_rate,
                channels=channels,
                device=output_device,
                cancel=cancel,
                block_ms=block_ms,
            )
        except DialogueError as error:
            failures.append(f"{error.failure}: {error.detail}")
            return
        playback_result["played_ms"] = result.played_ms
        playback_result["cancelled"] = result.cancelled
        playback_result["blocks"] = result.blocks

    thread = threading.Thread(target=_play, name="dialogue-echo-playback", daemon=True)
    thread.start()
    if lead_ms:
        dsp_thread_sleep(lead_ms)
    try:
        capture = backend.record(
            sample_rate=sample_rate,
            channels=channels,
            device=input_device,
            duration_ms=probe_ms + lead_ms,
            cancel=cancel,
            block_ms=block_ms,
        )
    except DialogueError as error:
        failures.append(f"{error.failure}: {error.detail}")
        capture = None
    thread.join(timeout=(probe_ms + lead_ms) / 1000.0 + 2.0)

    notes: list[str] = []
    if playback_result.get("cancelled"):
        notes.append("playback was cancelled before the probe finished")
    if capture is not None and capture.cancelled:
        notes.append("capture was cancelled before the probe finished")
    played_ms = int(playback_result.get("played_ms", 0))
    if capture is None:
        verdict: EchoVerdict = "stream_error"
        rms = peak = ratio = 0.0
        recorded_ms = 0
    else:
        recorded_ms = capture.duration_ms
        rms, peak, ratio, verdict = analyse(
            capture.samples,
            sample_rate=sample_rate,
            channels=channels,
            tone_hz=tone_hz,
            min_ratio=min_ratio,
            min_rms=min_rms,
        )
        capture.discard()  # 判定完就丢，绝不留下录音
    if failures:
        verdict = "stream_error"
        notes.extend(failures)
    if thread.is_alive():
        notes.append("playback thread did not finish before the timeout")
    return EchoReport(
        output_device=output_device.name,
        input_device=input_device.name,
        tone_hz=tone_hz,
        probe_ms=probe_ms,
        played_ms=played_ms,
        recorded_ms=recorded_ms,
        rms=round(rms, 6),
        peak=round(peak, 6),
        tone_ratio=round(ratio, 6),
        min_ratio=min_ratio,
        min_rms=min_rms,
        verdict=verdict,
        notes=tuple(notes),
    )


def dsp_thread_sleep(milliseconds: int) -> None:
    """让播放线程先起来（真实设备打开流有几十毫秒的启动延迟）。"""
    threading.Event().wait(max(0, milliseconds) / 1000.0)
