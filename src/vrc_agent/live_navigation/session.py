"""生产装配入口与固定任务汇总。没有备用传输、自动 ARM 或自动换任务。"""

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from vrc_agent.capture.live import live_capture
from vrc_agent.clock import SystemClock
from vrc_agent.config import AppConfig, load_config
from vrc_agent.contracts import ActionResult, Entity
from vrc_agent.control.service import ControllerProcess
from vrc_agent.perception.interpreter import Perception
from vrc_agent.providers.factory import build_provider
from vrc_agent.telemetry.budget import SessionBudget
from vrc_agent.telemetry.report import (
    aggregate_usage,
    elapsed_ms_between,
    hourly_from_usage,
    usage_defect,
)

from .parameters import action_freshness_ms
from .runtime import SupervisedNavigation
from .skill import CalibratedSkill, calibrated_prompt
from .stability import load_policy
from .stability_report import validate_stability_report
from .stable_runtime import stable_navigation_class
from .world import load_live_world


async def navigation_entry(args):
    world = load_live_world(getattr(args, "world_version", "v1"))
    task = world.task(args.task)
    landmark = world.landmark(task.group)
    if args.mode != "live":
        return {
            "mode": "plan-only",
            "sends_live_input": False,
            "suite_digest": world.digest(),
            "world": world.model_dump(mode="json", exclude={"tasks"}),
            "task": task.model_dump(mode="json"),
            "note": "Live requires explicit mode, calibrated environment, real vision, image/paid consent and local F9 ARM.",
        }
    config = load_config(args.config)
    if not args.confirm_calibration or not args.allow_paid or not args.send_images:
        raise ValueError("live requires calibration confirmation, paid and image consent")
    if config.perception.provider != "openai_compat" or config.perception.budget_usd <= 0:
        raise ValueError("live requires a real image provider and positive explicit budget")
    if config.perception.model == "SET_YOUR_VISION_MODEL":
        raise ValueError("select a real vision model before live startup")
    if config.control.enable_motion_probe:
        raise ValueError("manual probes must be disabled during supervised navigation")
    if (
        not config.capture.require_unoccluded
        or not config.capture.require_dpi_awareness
        or config.capture.window_match != "exact"
        or config.capture_window_title() != config.control.window_title
    ):
        raise ValueError(
            "live navigation requires exact, unoccluded, DPI-aware capture of the controlled window"
        )
    # 所有危险能力门禁在创建控制器/采集/供应商网络请求前检查。
    data = config.model_dump()
    data["mode"] = "live"
    data["perception"]["send_images"] = True
    data["perception"]["max_retries"] = 0
    stable = config.navigation_design != "legacy"
    policy_version = {
        "stable-inference-v1": "v1",
        "stable-inference-v2": "v2",
        "stable-inference-v3": "v3",
        "stable-inference-v4": "v4",
        "stable-inference-v5": "v5",
        "stable-inference-v6": "v6",
        "stable-inference-v7": "v7",
        "stable-inference-v8": "v8",
        "stable-inference-v9": "v9",
    }.get(config.navigation_design, "v1")
    if stable and load_policy(policy_version)[0]["status"] != "FROZEN":
        raise ValueError(
            "stability policy requires field calibration and a newly frozen version; "
            "candidate policies cannot enable live navigation"
        )
    data["perception"]["timeout_ms"] = (
        load_policy(policy_version)[0]["inference_ms"]
        if stable
        else min(1000, config.perception.timeout_ms)
    )
    config = AppConfig.model_validate(data)
    clock = SystemClock()
    budget = SessionBudget.from_config(config.perception)
    provider = build_provider(
        config.perception, clock=clock, accounting=budget, prepare_in_thread=stable
    )
    perception = Perception(
        provider,
        clock=clock,
        prompt=calibrated_prompt(landmark),
        max_observation_age_ms=action_freshness_ms(
            config, load_policy(policy_version)[0] if stable else None
        ),
    )
    report_dir = Path(args.state_dir) / "navigation-live"
    report_dir.mkdir(parents=True, exist_ok=True)
    with ControllerProcess(config, Path(args.state_dir)) as client:
        print(
            "Navigation DISARMED. Confirm task pose in VRChat. F9=ARM, F8=ESTOP. "
            "One task only; no automatic re-arm. Waiting up to 60s.",
            file=sys.stderr,
            flush=True,
        )
        runtime_class = stable_navigation_class(policy_version) if stable else SupervisedNavigation
        runtime = runtime_class(
            client,
            lambda authority: live_capture(
                session_id=client.session_id,
                config=config,
                clock=clock,
                environment_epoch=authority["environment_epoch"],
            ),
            perception,
            world,
            task,
            inference_ms=1000 if stable else config.perception.timeout_ms,
        )
        report = await runtime.run()
        report["calibration_confirmed"] = True
        report["provider"] = config.perception.provider
        report["model"] = config.perception.model
        report["usage"] = budget.snapshot()
        report["hourly"] = hourly_from_usage(
            elapsed_ms_between(report.get("started_at"), report.get("finished_at")),
            report["usage"],
        )
        # 每次尝试独立记录，不覆盖前次失败；只有元数据和结构化证据。
        path = report_dir / f"{task.task_id}-{client.session_id}.json"
        with path.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def summarize_reports(directory: Path, *, world_version="v1") -> dict:
    """汇总一批实机报告；逐份严格校验来源、台账与时间戳（复核 R5）。

    坏样本（来源不符、outcome 非字符串、台账非法、时间戳无时区或倒序）整份计入
    `invalid_files` 并附原因，不参与成功率也不参与折算——旧实现让畸形数值在文件级
    `try` 之外抛出，或用「混合有/无时区」的时间戳抛 `TypeError` 打断整份汇总。
    时长与用量的覆盖情况分别用 `duration_complete`、`usage.complete` 与
    `hourly.coverage` 如实标出，不再出现「分子有、分母无」的折算。
    """
    world = load_live_world(world_version)
    grouped = defaultdict(list)
    invalid: list[str] = []
    invalid_reasons: list[str] = []
    reports: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(report, dict)
                or report.get("suite_digest") != world.digest()
                or report.get("mode") != "live"
                or report.get("sends_live_input") is not True
            ):
                raise ValueError("report provenance mismatch")
            # v2 报告必须显式声明版本；v1 兼容早期未携带版本字段的报告。
            if (world_version == "v2" or "world_version" in report) and report.get(
                "world_version"
            ) != world.version:
                raise ValueError("report world version mismatch")
            task = world.task(report.get("task_id"))
            validate_stability_report(report)
            if not isinstance(report.get("outcome"), str):
                raise ValueError("invalid outcome")
            usage = report.get("usage")
            if usage is not None:
                if not isinstance(usage, dict):
                    raise ValueError("telemetry usage is not an object")
                defect = usage_defect(usage)
                if defect is not None:
                    raise ValueError(f"invalid usage telemetry: {defect}")
            started = _aware_stamp(report.get("started_at"), "started_at")
            finished = _aware_stamp(report.get("finished_at"), "finished_at")
            if started and finished and finished < started:
                raise ValueError("finished_at precedes started_at")
            grouped[task.task_id].append(report)
            reports.append(report)
        except Exception as exc:  # noqa: BLE001 — 单个坏文件不得打断整批（复核第 28 节 R5b）
            # 这里刻意兜住所有异常：`json` 解出巨型整数、报告字段被换成怪类型等都会在
            # 校验途中抛 ValueError/TypeError/OverflowError，逐个列异常类型必然漏。
            # 异常文本要截断，避免把整份载荷（可能含敏感串）写进汇总输出。
            invalid.append(path.name)
            if len(invalid_reasons) < 20:
                detail = " ".join(str(exc).split())[:200] or type(exc).__name__
                invalid_reasons.append(f"{path.name}: {type(exc).__name__}: {detail}")
    trials = []
    for task in world.tasks:
        attempts = grouped[task.task_id]
        reason = "NOT_RUN" if not attempts else "DUPLICATE_ATTEMPTS"
        if len(attempts) == 1:
            report = attempts[0]
            reason = report.get("outcome", "INVALID_REPORT")
            if report.get("final_state") == "ESTOP":
                reason = "MANUAL_STOP"
            if reason == "SUCCESS":
                if not verified_success(world, report):
                    reason = "UNVERIFIED"
        trials.append({"task_id": task.task_id, "success": reason == "SUCCESS", "outcome": reason})
    successes = sum(t["success"] for t in trials)
    durations = [
        elapsed_ms_between(report.get("started_at"), report.get("finished_at"))
        for report in reports
    ]
    elapsed_ms = sum(value for value in durations if value)
    duration_complete = bool(reports) and all(value for value in durations)
    usage = aggregate_usage(reports)
    # 文件级拒绝（来源不符、时间戳无时区/倒序、台账非法）不进入 `reports`，但如果不在
    # 这里合并，折算会看起来「覆盖完整」——坏样本必须同时体现在使用率的完整性标志上。
    usage["reports_invalid_files"] = len(invalid)
    usage["invalid_files"] = list(invalid)
    if invalid:
        usage["complete"] = False
        usage["cost_complete"] = False
        usage["tokens_complete"] = False
    return {
        "world_version": world.version,
        "suite_digest": world.digest(),
        "total": 20,
        "successes": successes,
        "success_rate": successes / 20,
        "invalid_files": invalid,
        "invalid_reasons": invalid_reasons,
        "duration_complete": duration_complete,
        "reports_missing_duration": sum(1 for value in durations if not value),
        "threshold_met": successes >= 16
        and not invalid
        and all(len(grouped[t.task_id]) == 1 for t in world.tasks),
        "field_acceptance": "REQUIRES_HUMAN_REVIEW",
        "elapsed_ms": elapsed_ms,
        "usage": usage,
        "hourly": hourly_from_usage(elapsed_ms, usage),
        "trials": trials,
    }


def _aware_stamp(value: object, name: str) -> datetime | None:
    """把 ISO 时间戳解析成**带时区**的 `datetime`；缺失返回 `None`，否则抛出 `ValueError`。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} is not an ISO timestamp string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} is not parseable: {exc}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} has no timezone offset")
    return parsed


def verified_success(world, report):
    """汇总也复验数值证据；不是日志的数字签名或人工现场确认。"""
    try:
        if report.get("suite_digest") != world.digest():
            return False
        if (world.version != "vrchat-home-live-v1" or "world_version" in report) and report.get(
            "world_version"
        ) != world.version:
            return False
        if report.get("calibration_confirmed") is not True or report.get("final_state") != "PAUSED":
            return False
        result = ActionResult.model_validate_json(json.dumps(report["results"][-1]))
        action = report["actions"][-1]
        if (
            result.status != "COMPLETED"
            or result.reason_code != "OK"
            or result.verification != "SUCCESS"
            or result.action_id != action["action_id"]
            or result.session_id != report["session_id"]
            or result.session_id != action["session_id"]
            or result.finished_at_ns is None
            or len(set(result.evidence_observation_ids)) != 2
        ):
            return False
        skill = CalibratedSkill(world.landmark(world.task(report["task_id"]).group))
        evidence = []
        for obs_id in result.evidence_observation_ids:
            observations = [
                e
                for e in report["events"]
                if e.get("event") == "observation" and e.get("observation_id") == obs_id
            ]
            if len(observations) != 1:
                return False
            obs = observations[0]
            if (
                obs["has_hazards"]
                or obs["has_uncertainties"]
                or obs["confidence"] < skill.goal.min_confidence
            ):
                return False
            matches = [
                Entity.model_validate_json(json.dumps(e))
                for e in obs["entities"]
                if e.get("label") == skill.goal.label
            ]
            if len(matches) != 1:
                return False
            entity = matches[0]
            if (
                entity.evidence_observation_id != obs_id
                or entity.last_seen_ns != obs["captured_at_ns"]
                or entity.confidence < skill.goal.min_confidence
                or not skill.arrived(entity)
            ):
                return False
            evidence.append((obs["captured_at_ns"], entity.track_id))
        return (
            evidence[0][0] < evidence[1][0]
            and evidence[1][0] > result.finished_at_ns
            and evidence[0][1] == evidence[1][1]
        )
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        return False
