"""现场配置、视觉判据、图像编码和 CLI 小任务的反例。"""

import ast
import asyncio
import base64
import io
import json
import struct
import zlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from vrc_agent.cli import main, print_json
from vrc_agent.clock import FakeClock
from vrc_agent.contracts import Entity, SceneState
from vrc_agent.control.service import startup_failure_detail
from vrc_agent.live_navigation.session import navigation_entry, summarize_reports
from vrc_agent.live_navigation.skill import CalibratedSkill, calibrated_prompt
from vrc_agent.live_navigation.world import LiveWorld, load_live_world
from vrc_agent.providers.base import Deadline, ModelInput
from vrc_agent.providers.images import bgra_png
from vrc_agent.providers.openai_compat import OpenAICompatibleProvider


def scene_for(skill, box=(0.05, 0.15, 0.95, 0.85), *, floor=True):
    entities = [
        Entity(
            track_id="goal",
            label=skill.goal.label,
            bbox=box,
            confidence=0.99,
            evidence_observation_id="obs",
            last_seen_ns=100,
        )
    ]
    if floor:
        entities += [
            Entity(
                track_id=label,
                label=label,
                bbox=(0.1, 0.6, 0.9, 0.98),
                confidence=0.99,
                evidence_observation_id="obs",
                last_seen_ns=100,
            )
            for label in ("forward_clearance", "recovery_clearance")
        ]
    return SceneState(
        session_id="test",
        observation_ids=("obs",),
        generated_at_ns=100,
        environment_epoch=1,
        confidence=0.99,
        entities=tuple(entities),
    )


def test_frozen_world_twenty_and_explicit_replacements():
    world = load_live_world()
    assert world.digest() == "dda1d3b1dce2605df37a9f4cf4a68cea5e1e639e7037351fb0c4b54bdfb7e335"
    assert len(world.tasks) == 20
    assert "蓝色" in world.landmark("G1").description
    assert all(
        "木地板" in item.recovery_zone and "?" not in item.description for item in world.landmarks
    )
    for group in ("G2", "G3"):
        task = world.task(group + "-4")
        assert task.condition == "front_repeat" and task.replacement_reason
        assert task.start_reference == world.task(group + "-1").start_reference
    assert world.task("G1-4").condition == "partial_occlusion"


@pytest.mark.parametrize(
    "fault", ["missing", "duplicate", "group", "world", "map", "zero_lower_area"]
)
def test_live_profile_rejects_invalid_freeze(fault):
    data = load_live_world().model_dump(mode="json")
    if fault == "missing":
        data["tasks"].pop()
    elif fault == "duplicate":
        data["tasks"][1] = data["tasks"][0]
    elif fault == "group":
        data["tasks"][0]["group"] = "G5"
    elif fault == "world":
        data["world_id"] = "other"
    elif fault == "map":
        data["coordinates"] = [0, 0]
    else:
        data["landmarks"][-1]["arrival"]["min_lower_coverage"] = 0.0
    with pytest.raises(ValidationError):
        LiveWorld.model_validate_json(json.dumps(data))


def test_g3_uses_frame_not_reflection_and_g5_lower_half():
    world = load_live_world()
    mirror = CalibratedSkill(world.landmark("G3"))
    assert "wooden panel frame" in calibrated_prompt(mirror.landmark)
    assert not mirror.arrived(scene_for(mirror, (0.45, 0.4, 0.55, 0.8)).entities[0])
    assert mirror.arrived(scene_for(mirror, (0.1, 0.05, 0.9, 0.95)).entities[0])
    floor = CalibratedSkill(world.landmark("G5"))
    assert not floor.arrived(scene_for(floor, (0.1, 0.0, 0.9, 0.4)).entities[0])
    assert floor.arrived(scene_for(floor, (0.1, 0.55, 0.9, 0.95)).entities[0])


@pytest.mark.parametrize("fault", ["absent", "small", "old", "low", "hazard", "uncertain"])
def test_recovery_requires_current_positive_floor_evidence(fault):
    skill = CalibratedSkill(load_live_world().landmark("G1"))
    scene = scene_for(skill)
    if fault == "absent":
        scene = scene_for(skill, floor=False)
    elif fault in {"hazard", "uncertain"}:
        scene = scene.model_copy(
            update={"hazards" if fault == "hazard" else "uncertainties": ("unsafe",)}
        )
    else:
        values = {
            "small": {"bbox": (0.45, 0.75, 0.55, 0.85)},
            "old": {"evidence_observation_id": "old"},
            "low": {"confidence": 0.5},
        }
        scene = scene.model_copy(
            update={
                "entities": (
                    *scene.entities[:-1],
                    scene.entities[-1].model_copy(update=values[fault]),
                )
            }
        )
    assert not skill.recovery_allowed(scene)
    skill._recovery.append("recover_strafe")
    proposal = asyncio.run(skill.decide(scene, ()))
    assert proposal.kind != "motion"


def test_forward_clearance_absent_waits_then_halts():
    skill = CalibratedSkill(load_live_world().landmark("G2"))
    scene = scene_for(skill, (0.45, 0.45, 0.55, 0.55), floor=False)
    for _ in range(3):
        assert asyncio.run(skill.decide(scene, ())).kind == "wait"
    assert skill.halt_reason == "PATH_UNSAFE"


def test_png_has_valid_chunks_colors_and_scanlines():
    raw = bytes([1, 2, 3, 255, 4, 5, 6, 255])
    png = bgra_png(raw, (2, 1))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    chunks = {}
    while pos < len(png):
        length = struct.unpack(">I", png[pos : pos + 4])[0]
        kind = png[pos + 4 : pos + 8]
        body = png[pos + 8 : pos + 8 + length]
        assert (
            zlib.crc32(kind + body) & 0xFFFFFFFF
            == struct.unpack(">I", png[pos + 8 + length : pos + 12 + length])[0]
        )
        chunks[kind] = body
        pos += length + 12
    assert struct.unpack(">II", chunks[b"IHDR"][:8]) == (2, 1)
    assert zlib.decompress(chunks[b"IDAT"]) == bytes([0, 3, 2, 1, 6, 5, 4])


def test_real_request_encodes_bgra_and_supplies_observation_id():
    provider = OpenAICompatibleProvider(
        model="test", api_key="not-real", send_images=True, clock=FakeClock(0)
    )
    request, _ = provider._build_request(
        ModelInput(
            prompt="describe",
            observation_id="actual-frame-7",
            request_id="request",
            image=bytes([1, 2, 3, 255]) * 4,
            image_media_type="application/x-bgra",
            image_size=(2, 2),
        ),
        Deadline.unlimited(),
    )
    parts = json.loads(request.body)["messages"][1]["content"]
    assert "actual-frame-7" in parts[0]["text"]
    assert base64.b64decode(parts[1]["image_url"]["url"].split(",")[1]).startswith(
        b"\x89PNG\r\n\x1a\n"
    )


def test_json_gbk_and_ascii_stdout_do_not_corrupt_data(monkeypatch):
    for encoding in ("gbk", "ascii", "utf-8"):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding=encoding)
        monkeypatch.setattr("sys.stdout", stream)
        print_json({"device": "设备 ® 😀"})
        stream.flush()
        assert json.loads(buffer.getvalue().decode(encoding)) == {"device": "设备 ® 😀"}


def test_startup_detail_is_specific_without_raw_secret():
    assert "hotkey" in startup_failure_detail(
        OSError("required global hotkey is already in use"), "hotkeys"
    )
    assert "lock" in startup_failure_detail(PermissionError(13, "secret credential"), "lock")
    assert "secret" not in startup_failure_detail(OSError("secret credential"), "osc")


def test_dry_plan_does_not_construct_controller_or_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("plan must be inert")

    monkeypatch.setattr("vrc_agent.live_navigation.session.ControllerProcess", forbidden)
    monkeypatch.setattr("vrc_agent.live_navigation.session.build_provider", forbidden)
    args = SimpleNamespace(task="G2-4", mode="dry-run")
    assert asyncio.run(navigation_entry(args))["mode"] == "plan-only"


@pytest.mark.parametrize("consent", ["confirm_calibration", "allow_paid", "send_images"])
def test_missing_live_consent_fails_before_controller(monkeypatch, consent):
    def forbidden(*args, **kwargs):
        raise AssertionError("no controller on failed consent")

    monkeypatch.setattr("vrc_agent.live_navigation.session.ControllerProcess", forbidden)
    args = SimpleNamespace(
        task="G1-1",
        mode="live",
        config=None,
        confirm_calibration=True,
        allow_paid=True,
        send_images=True,
    )
    setattr(args, consent, False)
    with pytest.raises(ValueError):
        asyncio.run(navigation_entry(args))


def test_live_does_not_accept_fake_provider(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("no controller with fake vision")

    monkeypatch.setattr("vrc_agent.live_navigation.session.ControllerProcess", forbidden)
    args = SimpleNamespace(
        task="G1-1",
        mode="live",
        config=None,
        confirm_calibration=True,
        allow_paid=True,
        send_images=True,
    )
    with pytest.raises(ValueError):
        asyncio.run(navigation_entry(args))


def test_live_root_hands_the_session_budget_to_the_provider(monkeypatch, tmp_path):
    """R3：live 组合根把 `SessionBudget` 本体交给供应商，而不是裸台账。"""
    from vrc_agent.live_navigation import session as live_session
    from vrc_agent.telemetry.budget import SessionBudget

    seen: dict = {}

    class Wired(Exception):
        """装配完成的哨兵：在创建控制器之前停止本用例。"""

    def spy(config, **kwargs):
        seen.update(kwargs)
        raise Wired

    monkeypatch.setattr(live_session, "build_provider", spy)
    config_path = tmp_path / "live.toml"
    config_path.write_text(
        '[perception]\nprovider = "openai_compat"\nmodel = "vision-real-1"\nbudget_usd = 5.0\n',
        encoding="utf-8",
    )
    args = SimpleNamespace(
        task="G1-1",
        mode="live",
        config=config_path,
        confirm_calibration=True,
        allow_paid=True,
        send_images=True,
        state_dir=str(tmp_path),
    )

    with pytest.raises(Wired):
        asyncio.run(navigation_entry(args))

    assert isinstance(seen.get("accounting"), SessionBudget), seen
    assert "ledger" not in seen


def test_no_auto_arm_or_replay_bypass_in_live_modules():
    import vrc_agent.live_navigation as package

    for path in Path(package.__file__).parent.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"arm", "reset"}
                if (
                    node.func.attr in {"rpc", "request"}
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                ):
                    assert node.args[0].value not in {"arm", "reset"}
            if isinstance(node, ast.ImportFrom):
                assert not any(s in (node.module or "") for s in ("transport", "control.replay"))


def test_summary_missing_and_duplicate_attempts_are_failures(tmp_path):
    world = load_live_world()
    assert summarize_reports(tmp_path)["success_rate"] == 0
    report = dict(
        mode="live",
        sends_live_input=True,
        suite_digest=world.digest(),
        task_id="G1-1",
        outcome="MANUAL_STOP",
        final_state="ESTOP",
    )
    (tmp_path / "a.json").write_text(json.dumps(report), encoding="utf-8")
    result = summarize_reports(tmp_path)
    assert result["total"] == 20 and result["trials"][0]["outcome"] == "MANUAL_STOP"
    (tmp_path / "b.json").write_text(json.dumps(report), encoding="utf-8")
    assert summarize_reports(tmp_path)["trials"][0]["outcome"] == "DUPLICATE_ATTEMPTS"


def test_cli_plan_and_empty_summary(capsys, tmp_path):
    assert main(["navigate-live", "--task", "G1-1"]) == 0
    assert json.loads(capsys.readouterr().out)["mode"] == "plan-only"
    assert main(["navigation-report", "--report-dir", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["successes"] == 0


def test_supervised_reports_fold_usage_and_hourly(tmp_path):
    """实机报告口径：台账逐份折叠、按记录时长折算每小时、缺 usage 不当 0 美元。"""
    world = load_live_world()
    first, second, third = (task.task_id for task in world.tasks[:3])
    base = {
        "mode": "live",
        "sends_live_input": True,
        "suite_digest": world.digest(),
        "outcome": "MODEL_TIMEOUT",
    }
    timed = [
        (first, "2026-10-15T10:00:00+00:00", "2026-10-15T10:00:30+00:00"),
        (second, "2026-10-15T11:00:00+00:00", "2026-10-15T11:00:30+00:00"),
        (third, "2026-10-15T12:00:00+00:00", "2026-10-15T12:00:30+00:00"),
    ]
    budgets = [
        {
            "calls": 2,
            "tokens_known": 200,
            "unknown_token_calls": 0,
            "image_count": 2,
            "spent_usd_known": 0.2,
            "unknown_cost_calls": 0,
            "cost_complete": True,
            "tokens_complete": True,
            "by_provider": {"openai_compat": 2},
        },
        {
            "calls": 1,
            "tokens_known": 50,
            "unknown_token_calls": 0,
            "image_count": 1,
            "spent_usd_known": 0.1,
            "unknown_cost_calls": 0,
            "cost_complete": True,
            "tokens_complete": True,
            "by_provider": {"openai_compat": 1},
        },
    ]
    for (task_id, started, finished), usage in zip(timed[:2], budgets):
        document = {**base, "task_id": task_id, "started_at": started, "finished_at": finished}
        document["usage"] = usage
        (tmp_path / f"{task_id}.json").write_text(json.dumps(document), encoding="utf-8")
    task_id, started, finished = timed[2]
    (tmp_path / f"{task_id}.json").write_text(
        json.dumps({**base, "task_id": task_id, "started_at": started, "finished_at": finished}),
        encoding="utf-8",
    )
    preview = dict(base, task_id=first, mode="supervised-dry-run", sends_live_input=False)
    (tmp_path / "preview.json").write_text(json.dumps(preview), encoding="utf-8")

    result = summarize_reports(tmp_path)

    assert result["invalid_files"] == ["preview.json"]
    # 复核 R5：被拒的文件也必须体现在覆盖里，否则折算会看起来「只缺一份 usage」。
    assert result["usage"]["reports_invalid_files"] == 1
    assert result["usage"]["invalid_files"] == ["preview.json"]
    assert result["usage"]["calls"] == 3
    assert result["usage"]["spent_usd_known"] == 0.3
    assert result["usage"]["by_provider"] == {"openai_compat": 3}
    assert result["usage"]["reports_with_usage"] == 2
    assert result["usage"]["reports_without_usage"] == 1
    assert result["usage"]["reports_invalid_usage"] == 0
    assert result["usage"]["cost_complete"] is False
    assert result["usage"]["complete"] is False
    assert result["elapsed_ms"] == 90_000
    assert result["duration_complete"] is True
    # 复核 R5：折算的分子（两份台账）与分母（**带台账那两份**报告的时长）同源 = 60 s，
    # 不再拿 90 s 去摊 60 s 的用量；覆盖情况如实写在 coverage 里。
    assert result["hourly"]["basis_ms"] == 60_000
    assert result["hourly"]["calls_per_hour"] == 180.0
    assert result["hourly"]["cost_per_hour_usd"] is None
    assert result["hourly"]["coverage"] == {
        "source": "aggregated_reports",
        "reports_with_usage": 2,
        "reports_without_usage": 1,
        "reports_invalid_usage": 0,
        "reports_invalid_files": 1,
        "reports_paired": 2,
        "reports_unpaired_duration": 0,
        "basis_ms_with_usage": 60_000,
        "session_elapsed_ms": 90_000,
        "complete": False,
    }
    assert str(result["hourly"]["reason"]).startswith("COST_INCOMPLETE")
