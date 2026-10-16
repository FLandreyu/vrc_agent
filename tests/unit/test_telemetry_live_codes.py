"""T08 N1 / F3 回归：live 受监管导航的结局码必须登记进遥测码表。

背景（`docs/TASKS.md` 第 42 节 N1、第 37 节 F3）：v1 legacy live、方向 A 与 v2 候选的结局码
此前只是源码里的字符串字面量，`telemetry.codes` 里没有它们。后果有两条：

1. **报告侧**：`report`/`navigation-report` 用 `codes_of` 从 `outcome`/`reason_code` 里捞码，
   未登记的值落进 `codes.unknown_codes` 并生成 `unknown error codes present: …` 警告；
   于是「出现未知码」这个本该提示新缺陷的信号，被每次 live 运行都刷一遍的噪音淹没。
2. **事件侧**：`EventLog` 的枚举字段只接受登记值，写 `reason_code=<未登记码>` 会整字段被拒
   （`rejected_reasons` 里留下 `reason_code:unregistered_value`）。

这里锁四件事：

- 登记表覆盖层内闭集，且**闭集与源码一致**（少登记会失败；
  登记了却在源码里找不到的「死码」也会失败）；
- 层源码里新出现的大写字面量必须被显式分类——是结局码就登记，不是就写进
  `NON_CODE_LITERALS` 并给出理由（下次再漏会在这里失败，而不是等运维在报告里发现）；
- `MODEL_*` 派生码跟随供应商闭集（供应商新增失败码时自动登记）；
- 端到端验收：`EventLog` 真的接受这些码，`tally` 真的把它们算进 `live_navigation` 家族，
  而真正的未知码仍然被单独列出（信号没有被这次登记弄哑）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import vrc_agent
from vrc_agent.live_navigation.errors import (
    LIVE_NAVIGATION_CODES,
    LIVE_NAVIGATION_FAILURES,
    LIVE_SUMMARY_OUTCOMES,
    MODEL_FAILURE_CODES,
)
from vrc_agent.providers.errors import PROVIDER_FAILURES
from vrc_agent.telemetry import EventLog
from vrc_agent.telemetry import codes as tcode

PACKAGE = Path(vrc_agent.__file__).resolve().parent / "live_navigation"

#: 闭集自己所在的文件：只声明码、不使用码，扫描时排除（否则「源码里还能找到」永远成立）。
DECLARATIONS = "errors.py"

#: 码形状的大写字面量：长度 ≥3、全大写、可含数字与下划线。
CODE_SHAPED = re.compile(r'"([A-Z][A-Z0-9_]{2,})"')

#: **不是错误码**的大写字面量。每条都必须给出理由——新增一个就说明「为什么它不算码」，
#: 而不是把它塞进登记表凑绿。
NON_CODE_LITERALS: dict[str, str] = {
    "ARMED_IDLE": "控制进程状态名（`state`/`final_state` 字段），不是结局码",
    "DISARMED": "控制进程状态名",
    "EXECUTING": "控制进程状态名",
    "RUNNING": "控制进程状态名",
    "ESTOP": "控制进程状态名（急停后的 `final_state`）",
    "COMPLETED": "动作结果 `status` 字段值",
    "FROZEN": "v1 稳定性报告 `status` 字段值",
    "REQUIRES_HUMAN_REVIEW": "`field_acceptance` 处置标记",
    "ENGINEERING_INITIAL_FIELD_CHECK_REQUIRED": "世界配置 `ArrivalRule.status`",
    "SET_YOUR_VISION_MODEL": "配置占位哨兵（选了真模型才允许 live 启动）",
    "MODEL_": "结局码前缀，与 `PROVIDER_FAILURES` 组合成 `MODEL_*` 码",
    "PENDING_FIELD_CAPTURE": "世界 v2 离线提案的 reference_state，表示尚未拍摄参考图，不是运行结局码",
    "PROPOSAL_NOT_APPROVED": "世界 v2 离线提案的 status，不是运行结局码或冻结许可",
}


def _literals() -> dict[str, list[str]]:
    """`live_navigation` 包内（除声明文件）出现的码形状字面量 → 出现的文件。"""
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.glob("*.py")):
        if path.name == DECLARATIONS:
            continue
        text = path.read_text(encoding="utf-8")
        for match in CODE_SHAPED.finditer(text):
            found.setdefault(match.group(1), []).append(path.name)
    return found


def test_every_code_shaped_literal_is_classified() -> None:
    found = _literals()
    assert found, "扫描不到任何码形状字面量：正则或目录布局已过期"

    known = set(tcode.KNOWN_CODES)
    unclassified = {
        code: sorted(set(files))
        for code, files in found.items()
        if code not in known and code not in NON_CODE_LITERALS
    }

    assert not unclassified, (
        "live 层出现未分类的大写字面量：是结局码就登记进 live_navigation.errors，"
        f"不是就写进 NON_CODE_LITERALS 并给出理由；未分类：{sorted(unclassified)}"
    )


def test_layer_closed_set_and_source_stay_in_sync() -> None:
    known = set(tcode.KNOWN_CODES)
    assert set(LIVE_NAVIGATION_CODES) <= known, sorted(set(LIVE_NAVIGATION_CODES) - known)

    # 直接写在源码里的码（`MODEL_*` 是拼出来的，见下一个用例）必须双向一致。
    declared = set(LIVE_NAVIGATION_FAILURES) | set(LIVE_SUMMARY_OUTCOMES)
    found = set(_literals())
    assert declared <= found, f"登记了但源码里已找不到（死码）：{sorted(declared - found)}"

    # 家族归属：派生的 `MODEL_TIMEOUT` 与离线导航批次共用，保留先登记的 `navigation`。
    for code in LIVE_NAVIGATION_CODES:
        family = tcode.family_of(code)
        expected = "navigation" if code == "MODEL_TIMEOUT" else "live_navigation"
        assert family == expected, f"{code} 归入 {family}，期望 {expected}"


def test_model_codes_track_provider_failures() -> None:
    assert MODEL_FAILURE_CODES == tuple(f"MODEL_{failure}" for failure in PROVIDER_FAILURES)
    assert set(MODEL_FAILURE_CODES) <= set(tcode.KNOWN_CODES)

    # 组合方式变了，这个派生就必须跟着改（而不是悄悄漏登记一批新码）。
    runtime_source = (PACKAGE / "runtime.py").read_text(encoding="utf-8")
    assert '"MODEL_" +' in runtime_source, "结局码不再是 `MODEL_` + 供应商失败码：请同步派生表"


def test_event_log_accepts_live_outcomes(tmp_path: Path) -> None:
    log = EventLog(tmp_path / "events.jsonl", "0" * 32)
    for code in LIVE_NAVIGATION_CODES:
        log.emit("navigation_outcome", reason_code=code)
    log.close()

    stats = log.stats()
    assert stats["rejected"] == 0, stats["rejected_reasons"]
    assert stats["written"] == len(LIVE_NAVIGATION_CODES)

    records = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {record["reason_code"] for record in records} == set(LIVE_NAVIGATION_CODES)


def test_report_tally_keeps_live_outcomes_known() -> None:
    samples = [
        {"outcome": "TASK_TIMEOUT"},
        {"reason_code": "MASKED_SCENE_EVIDENCE"},
        {"reason_code": "MODEL_TRANSPORT_ERROR"},
        {"trials": [{"outcome": "NOT_RUN"}, {"outcome": "DUPLICATE_ATTEMPTS"}]},
        {"trials": [{"outcome": "INVALID_REPORT"}]},
    ]
    report = tcode.tally(tcode.codes_of(*samples))

    assert report["unknown_codes"] == {}
    assert report["by_family"] == {"live_navigation": 6}

    # 登记不等于「什么都认识」：真正的未知码仍必须单独列出（T10 的信号不能被这次登记弄哑）。
    unknown = tcode.tally(tcode.codes_of({"outcome": "STABILITY_V3_CANDIDATE"}))
    assert unknown["unknown_codes"] == {"STABILITY_V3_CANDIDATE": 1}
    assert unknown["by_family"] == {}
