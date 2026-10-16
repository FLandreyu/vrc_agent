"""信号处理与设备选择：纯函数，不碰设备、不写文件。"""

from __future__ import annotations

import pytest

from vrc_agent.dialogue import (
    AudioDevice,
    DialogueError,
    describe_devices,
    dsp,
    match_devices,
    select_device,
    virtual_mic_hint,
)

RATE = 48_000


def device(
    index: int, name: str, *, in_channels: int = 0, out_channels: int = 0, hostapi: str = "MME"
) -> AudioDevice:
    return AudioDevice(
        index=index,
        name=name,
        hostapi=hostapi,
        max_input_channels=in_channels,
        max_output_channels=out_channels,
        default_samplerate=RATE,
    )


# ------------------------------------------------------------------ dsp


def test_sine_tone_has_the_requested_length_and_amplitude() -> None:
    tone = dsp.sine_tone(440.0, 100, RATE, channels=1, amplitude=0.5)
    assert dsp.duration_ms_of(tone, RATE, 1) == 100
    assert dsp.peak(tone) <= 0.5 + 1e-6
    assert dsp.rms(tone) > 0.2  # 满幅正弦的 RMS ≈ 0.35
    assert dsp.frames_of(tone, 1) == 4_800


def test_tone_ratio_is_near_one_for_a_pure_tone_and_low_for_another_frequency() -> None:
    tone = dsp.sine_tone(880.0, 200, RATE, amplitude=0.4)
    assert dsp.tone_ratio(tone, 880.0, RATE) > 0.98
    assert dsp.tone_ratio(tone, 440.0, RATE) < 0.05


def test_tone_ratio_uses_the_first_channel_of_stereo_audio() -> None:
    stereo = dsp.sine_tone(880.0, 300, RATE, channels=2, amplitude=0.4)
    # 淡入淡出会吃掉一点点纯度，所以给到 0.9 而不是贴着 1.0。
    assert dsp.tone_ratio(stereo, 880.0, RATE, channels=2) > 0.9
    assert dsp.tone_ratio(stereo, 440.0, RATE, channels=2) < 0.05


def test_silence_has_no_energy_and_tone_ratio_is_zero() -> None:
    quiet = dsp.silence(50, RATE, channels=1)
    assert dsp.rms(quiet) == 0.0
    assert dsp.tone_ratio(quiet, 880.0, RATE) == 0.0


def test_invalid_tone_parameters_are_rejected() -> None:
    with pytest.raises(ValueError):
        dsp.sine_tone(30_000.0, 100, RATE)  # 超过 Nyquist
    with pytest.raises(ValueError):
        dsp.sine_tone(440.0, 0, RATE)
    with pytest.raises(ValueError):
        dsp.sine_tone(440.0, 100, RATE, amplitude=2.0)
    with pytest.raises(ValueError):
        dsp.frames_of(dsp.silence(10, RATE), 0)


def test_hash_prefix_is_deterministic_and_short() -> None:
    tone = dsp.sine_tone(440.0, 20, RATE)
    assert dsp.hash_prefix(tone) == dsp.hash_prefix(tone)
    assert len(dsp.hash_prefix(tone)) == 16
    assert dsp.hash_prefix(tone) != dsp.hash_prefix(dsp.sine_tone(441.0, 20, RATE))


# ------------------------------------------------------------- devices


def test_name_matching_prefers_exact_then_substring() -> None:
    devices = (
        device(0, "Voicemeeter Input (VB-Audio Voicemeeter VAIO)", out_channels=8),
        device(1, "Voicemeeter AUX Input (VB-Audio Voicemeeter VAIO)", out_channels=8),
    )
    exact = match_devices(
        devices, "Voicemeeter AUX Input (VB-Audio Voicemeeter VAIO)", direction="output"
    )
    assert [d.index for d in exact] == [1]
    partial = match_devices(devices, "voicemeeter input", direction="output")
    # 子串匹配、大小写不敏感："Voicemeeter Input" 命中，AUX 那条不命中。
    assert [d.index for d in partial] == [0]
    assert [d.index for d in match_devices(devices, "VOICEMEETER", direction="output")] == [0, 1]


def test_matching_preserves_the_backend_order() -> None:
    # 后端已按「宿主 API 偏好 + 索引」排好；再按索引排序会让 WASAPI 败给低索引 MME。
    devices = (
        device(42, "Voicemeeter Input", out_channels=2, hostapi="Windows WASAPI"),
        device(14, "Voicemeeter Input", out_channels=8, hostapi="MME"),
    )
    picked = select_device(devices, "Voicemeeter Input", direction="output")
    assert picked.index == 42 and picked.hostapi == "Windows WASAPI"


def test_direction_and_hostapi_filters_are_applied() -> None:
    devices = (
        device(0, "Voice Out", out_channels=8, hostapi="MME"),
        device(1, "Voice Out", out_channels=2, hostapi="Windows WASAPI"),
        device(2, "Mic In", in_channels=2),
    )
    assert match_devices(devices, "Voice Out", direction="input") == ()
    assert [
        d.index for d in match_devices(devices, "Voice Out", direction="output", hostapi="MME")
    ] == [0]
    assert [d.index for d in match_devices(devices, None, direction="input")] == [2]


def test_missing_device_and_missing_channels_fail_loudly() -> None:
    devices = (device(0, "Speakers", out_channels=2),)
    with pytest.raises(DialogueError) as info:
        select_device(devices, "Nonexistent", direction="output")
    assert info.value.failure == "DEVICE_UNAVAILABLE"
    with pytest.raises(DialogueError):
        select_device(devices, "Speakers", direction="output", required_channels=4)
    with pytest.raises(DialogueError):
        select_device(devices, None, direction="input")


def test_describe_devices_and_virtual_mic_hint() -> None:
    devices = (
        device(
            1,
            "Voicemeeter Input (VB-Audio Voicemeeter VAIO)",
            out_channels=2,
            hostapi="Windows WASAPI",
        ),
        device(
            2,
            "Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)",
            in_channels=2,
            hostapi="Windows WASAPI",
        ),
        device(3, "Voicemeeter Out A1 (VB-Audio Voicemeeter VAIO)", in_channels=2),
        device(4, "本机扬声器", out_channels=8),
    )
    rows = describe_devices(devices)
    assert rows[0]["index"] == 1 and rows[0]["out"] == 2
    hint = virtual_mic_hint(devices)
    assert hint["playback_target_found"] is True
    assert hint["playback_target"].startswith("Voicemeeter Input")
    assert hint["loopback_candidates"] == ["Voicemeeter Out B1 (VB-Audio Voicemeeter VAIO)"]
    assert hint["routing_ready"] is True
    assert virtual_mic_hint((device(9, "Only Speakers", out_channels=2),))["routing_ready"] is False


def test_device_usable_directions() -> None:
    both = device(0, "Duplex", in_channels=2, out_channels=2)
    assert both.usable_for == ("input", "output")
    assert device(1, "Out only", out_channels=2).usable_for == ("output",)
    assert device(2, "In only", in_channels=1).usable_for == ("input",)
