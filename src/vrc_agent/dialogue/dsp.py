"""对话层用到的少量信号处理：只用标准库 `array` 与 `math`，不引入 numpy。

音频一律以 `array("f")`（float32）形式在内存里传递，**任何函数都不写文件**。
"""

from __future__ import annotations

import math
from array import array

MS = 1_000_000

Samples = array  # array("f")：交错排列的 float32 采样


def frames_of(samples: array, channels: int) -> int:
    if channels <= 0:
        raise ValueError("channels must be positive")
    return len(samples) // channels


def duration_ms_of(samples: array, sample_rate: int, channels: int) -> int:
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return frames_of(samples, channels) * 1000 // sample_rate


def sine_tone(
    frequency_hz: float,
    duration_ms: int,
    sample_rate: int,
    *,
    channels: int = 1,
    amplitude: float = 0.25,
    fade_ms: int = 5,
) -> array:
    """生成一段带淡入淡出的正弦音（淡入淡出避免爆音，也让取消点听不出来）。"""
    if frequency_hz <= 0 or frequency_hz >= sample_rate / 2:
        raise ValueError("frequency must be between 0 and the Nyquist limit")
    if duration_ms <= 0:
        raise ValueError("duration_ms must be positive")
    if channels <= 0:
        raise ValueError("channels must be positive")
    if not 0.0 < amplitude <= 1.0:
        raise ValueError("amplitude must be in (0, 1]")
    frames = duration_ms * sample_rate // 1000
    fade_frames = max(1, fade_ms * sample_rate // 1000)
    samples = array("f")
    step = 2 * math.pi * frequency_hz / sample_rate
    for index in range(frames):
        envelope = 1.0
        if index < fade_frames:
            envelope = index / fade_frames
        elif index >= frames - fade_frames:
            envelope = (frames - index) / fade_frames
        value = amplitude * envelope * math.sin(step * index)
        for _ in range(channels):
            samples.append(value)
    return samples


def silence(duration_ms: int, sample_rate: int, *, channels: int = 1) -> array:
    frames = max(1, duration_ms * sample_rate // 1000)
    return array("f", bytes(4 * frames * channels))


def rms(samples: array) -> float:
    if not samples:
        return 0.0
    total = 0.0
    for value in samples:
        total += value * value
    return math.sqrt(total / len(samples))


def peak(samples: array) -> float:
    return max((abs(value) for value in samples), default=0.0)


def _channel_zero(samples: array, channels: int) -> array:
    """取第一声道，便于按单声道做频率分析。"""
    if channels <= 1:
        return samples
    return array("f", samples[0::channels])


def goertzel_power(samples: array, frequency_hz: float, sample_rate: int) -> float:
    """单频功率（Goertzel 算法）。用来判断「录到的到底是不是刚才放的那段音」。"""
    data = samples
    count = len(data)
    if count == 0:
        return 0.0
    omega = 2 * math.pi * frequency_hz / sample_rate
    coefficient = 2 * math.cos(omega)
    previous = previous2 = 0.0
    for value in data:
        current = value + coefficient * previous - previous2
        previous2 = previous
        previous = current
    power = previous * previous + previous2 * previous2 - coefficient * previous * previous2
    return max(0.0, power) / (count * count)


def tone_ratio(
    samples: array, frequency_hz: float, sample_rate: int, *, channels: int = 1
) -> float:
    """目标频率占总能量的比例。

    实信号的能量会在 ±ω 两个 bin 上各分一半，所以这里乘 2 做单边修正：
    一段**纯**目标频率的正弦得分 ≈ 1.0，完全不是那个频率则接近 0。
    """
    data = _channel_zero(samples, channels)
    total = 0.0
    for value in data:
        total += value * value
    if total <= 0:
        return 0.0
    tone = 2.0 * goertzel_power(data, frequency_hz, sample_rate) * len(data)
    return min(1.0, max(0.0, tone / total))


def scaled(samples: array, gain: float) -> array:
    return array("f", (max(-1.0, min(1.0, value * gain)) for value in samples))


def hash_prefix(samples: array, length: int = 16) -> str:
    """采样摘要（用于「两次播放是不是同一段」的对账）；不是加密承诺。"""
    import hashlib

    digest = hashlib.sha256()
    digest.update(samples.tobytes())
    return digest.hexdigest()[:length]
