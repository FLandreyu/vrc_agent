"""live 受监管导航的结局码闭集（v1 legacy live、方向 A 候选与 v2 候选共用）。

这一层把控制进程、采集/技能层与供应商的失败折成「一次导航的结局」：这些值会落进报告的
`outcome` 字段（`report`/`navigation-report` 会经 `telemetry.codes.codes_of` 捞出来做
按家族计数），因此必须登记进遥测码表（`telemetry.codes` 的 `live_navigation` 家族）。
没登记时它们会被算成「未知码」并在每份报告的 `warnings` 里刷一条
`unknown error codes present: …` —— 这正是 T10 用来发现新缺陷的信号被噪音淹没
（TASKS 第 42 节 N1、第 37 节 F3）。

**本模块必须保持零依赖**（只用标准库与 `providers.errors`）：`telemetry.codes` 会 import
它，而 live 运行时本身依赖遥测，反向的依赖链会成环。

`tests/unit/test_telemetry_live_codes.py` 双向锁住：源码里写下的码字面量必须先在
`LIVE_NAVIGATION_FAILURES` / `LIVE_SUMMARY_OUTCOMES` 里登记，登记过的码也必须仍能在源码里
找到；供应商新增失败时 `MODEL_*` 元组自动跟随 `PROVIDER_FAILURES`。
"""

from __future__ import annotations

from typing import Literal

from vrc_agent.providers.errors import PROVIDER_FAILURES

#: 一次 live 导航的终止原因（`RuntimeHalt` 的原因、技能与稳定运行时的 `reason=`）。
LiveNavigationFailure = Literal[
    "TASK_TIMEOUT",
    "ARM_TIMEOUT",
    "CAPTURE_TIMEOUT",
    "CONTROL_TIMEOUT",
    "CALIBRATION_MISMATCH",
    "PATH_UNSAFE",
    "RECOVERY_UNSAFE",
    "CONTROL_UNAVAILABLE",
    "SOURCE_CLOSE_FAILED",
    "STABILITY_TIMEOUT",
    "IMAGE_ENCODING_TIMEOUT",
    "INFERENCE_TIMEOUT",
    "SCENE_UNSTABLE",
    "WARMUP_UNSTABLE",
    "MASKED_SCENE_EVIDENCE",
]

#: 与 `LiveNavigationFailure` 同集合的元组形式，供登记表与复核扫描使用。
LIVE_NAVIGATION_FAILURES: tuple[LiveNavigationFailure, ...] = (
    "TASK_TIMEOUT",
    "ARM_TIMEOUT",
    "CAPTURE_TIMEOUT",
    "CONTROL_TIMEOUT",
    "CALIBRATION_MISMATCH",
    "PATH_UNSAFE",
    "RECOVERY_UNSAFE",
    "CONTROL_UNAVAILABLE",
    "SOURCE_CLOSE_FAILED",
    "STABILITY_TIMEOUT",
    "IMAGE_ENCODING_TIMEOUT",
    "INFERENCE_TIMEOUT",
    "SCENE_UNSTABLE",
    "WARMUP_UNSTABLE",
    "MASKED_SCENE_EVIDENCE",
)

#: 20 次批次汇总里单个任务的 `outcome`：报告没跑、跑了两次、报告本身损坏。
#: 这三个值只出现在 `summarize_reports` 的 `trials[].outcome` 里，同样会被 `codes_of`
#: 当作候错误码。`SUCCESS`/`MANUAL_STOP`/`UNVERIFIED` 已分别登记在 runtime/control/navigation
#: 家族，这里不重复声明（登记表保留首个家族）。
LIVE_SUMMARY_OUTCOMES: tuple[str, ...] = (
    "NOT_RUN",
    "DUPLICATE_ATTEMPTS",
    "INVALID_REPORT",
)

#: live 运行时把供应商失败折成结局码：`"MODEL_" + ProviderError.failure`
#: （`live_navigation/runtime.py` 的 `except ProviderError` 分支）。派生自
#: `PROVIDER_FAILURES`，供应商新增失败码时这里自动跟随——`MODEL_*` 与其它家族的码
#: 同形，漏登记一样会被算成未知码。
MODEL_FAILURE_CODES: tuple[str, ...] = tuple(f"MODEL_{failure}" for failure in PROVIDER_FAILURES)

#: 本层结局码的完整集合（顺序稳定）：登记、报告复核与回归扫描都以它为准。
LIVE_NAVIGATION_CODES: tuple[str, ...] = (
    LIVE_NAVIGATION_FAILURES + LIVE_SUMMARY_OUTCOMES + MODEL_FAILURE_CODES
)

__all__ = [
    "LIVE_NAVIGATION_CODES",
    "LIVE_NAVIGATION_FAILURES",
    "LIVE_SUMMARY_OUTCOMES",
    "MODEL_FAILURE_CODES",
    "LiveNavigationFailure",
]
