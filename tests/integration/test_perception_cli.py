"""T05 命令行集成：离线感知、预算门禁与 doctor 的感知块。

这一层测试的是产品表面（退出码、JSON 形状、付费门禁），不打桩内部函数。
"""

from __future__ import annotations

import json
import socket
import urllib.request

import pytest
from pydantic import ValidationError

from vrc_agent.cli import main, perceive_run
from vrc_agent.config import AppConfig, PerceptionConfig, load_config


def run_cli(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict, str]:
    code = main(list(argv))
    captured = capsys.readouterr()
    payload = json.loads(captured.out) if captured.out.strip() else {}
    return code, payload, captured.err


def test_perceive_defaults_to_the_free_offline_fake_provider(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, err = run_cli(capsys, "perceive", "--max-steps", "3")

    assert code == 0
    assert err == ""
    assert payload["mode"] == "perceive-offline"
    assert payload["produces_actions"] is False
    assert payload["sends_input"] is False
    assert payload["writes_media"] is False
    assert payload["stopped_reason"] == "max_steps"
    assert payload["environment_epoch"] == 1

    provider = payload["provider"]
    assert provider["provider"] == "fake"
    assert provider["paid"] is False
    assert provider["offline"] is True
    assert provider["send_images"] is False

    # 免费供应商的 0 是真实值：费用账目完整，没有任何未知费用。
    assert payload["usage"]["calls"] == 3
    assert payload["usage"]["spent_usd_known"] == 0.0
    assert payload["usage"]["cost_complete"] is True
    assert payload["usage"]["unknown_cost_calls"] == 0
    assert payload["usage"]["image_count"] == 0

    assert len(payload["scenes"]) == 3
    for scene in payload["scenes"]:
        assert scene["window_state"] == "visible"
        assert scene["confidence"] == 0.6
        assert len(scene["entities"]) == 1
        assert scene["entities"][0]["evidence_observation_id"] == scene["observation_id"]
    # 感知与回放共用同一个虚拟时钟：时间戳就是素材里的采集时刻。
    assert [scene["generated_at_ns"] for scene in payload["scenes"]] == [
        1_000_000_000,
        1_150_000_000,
        1_300_000_000,
    ]

    assert payload["perception"]["calls"] == 3
    assert payload["perception"]["scenes"] == 3
    assert payload["capture"]["ok"] == 3


def test_the_perceive_output_cannot_carry_an_action(capsys: pytest.CaptureFixture[str]) -> None:
    _, payload, _ = run_cli(capsys, "perceive", "--max-steps", "1")
    scenes = json.dumps(payload["scenes"], ensure_ascii=False)
    for forbidden in ("lease", "expires_at_ns", "osc", "forward", "duration_ms", "kind"):
        assert forbidden not in scenes
    for scene in payload["scenes"]:
        assert "action" not in scene
        assert set(scene) <= {
            "scene_id",
            "observation_id",
            "window_state",
            "generated_at_ns",
            "confidence",
            "entities",
            "hazards",
            "uncertainties",
        }


def test_perceive_stops_at_the_end_of_the_fixture_instead_of_looping(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, payload, _ = run_cli(capsys, "perceive", "--max-steps", "100")

    assert code == 0
    assert payload["stopped_reason"] == "replay_exhausted"
    assert len(payload["scenes"]) == 13
    # 素材放完就是放完了：没有新观测，也就没有新场景，失败步只产生 unknown 场景。
    assert sum(1 for scene in payload["scenes"] if scene["entities"]) == 5
    assert payload["capture"]["attempts"] == 13
    assert payload["capture"]["ok"] == 5


def test_blind_capture_steps_become_unknown_scenes_not_invented_ones(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, payload, _ = run_cli(capsys, "perceive", "--max-steps", "13")

    by_state = {scene["window_state"]: 0 for scene in payload["scenes"]}
    for scene in payload["scenes"]:
        by_state[scene["window_state"]] += 1
        if scene["window_state"] != "visible":
            assert scene["confidence"] is None
            assert scene["entities"] == []
            assert scene["uncertainties"]  # 必须写明「为什么不知道」
    # 13 步素材里有 8 步是看不见的。
    assert sum(count for state, count in by_state.items() if state != "visible") == 8
    assert payload["perception"]["skipped_not_visible"] == 8
    assert payload["perception"]["scenes"] == 5


def test_a_paid_provider_needs_an_explicit_confirmation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = load_config(None)

    # 产品表面：拒绝执行（退出码 2）。注意 CLI 的参数错误只给通用提示，
    # 具体原因由下面这层负责说明 —— 通用提示是已知待改项，见验证记录。
    code, _, err = run_cli(capsys, "perceive", "--provider", "openai_compat")
    assert code == 2
    assert err.strip()

    # 预算给足之后，才轮到「本机用户是否明确确认」这道门。
    paid_config = AppConfig.model_validate(
        {
            **config.model_dump(),
            "perception": {
                **config.perception.model_dump(),
                "provider": "openai_compat",
                "budget_usd": 1.0,
            },
        }
    )
    with pytest.raises(ValueError, match="--allow-paid"):
        perceive_run(
            paid_config,
            fixture_path=None,
            max_steps=1,
            provider_name=None,
            allow_paid=False,
            send_images=False,
        )


def test_a_paid_provider_without_a_budget_is_refused_even_with_confirmation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # --allow-paid 只是本机用户的确认；没有正预算时配置层仍然拒绝，请求不会被发出去。
    code, _, err = run_cli(capsys, "perceive", "--provider", "openai_compat", "--allow-paid")
    assert code == 2
    assert err.strip()

    with pytest.raises(ValidationError, match="budget_usd"):
        PerceptionConfig.model_validate({"provider": "openai_compat"})


def test_the_max_steps_bounds_are_enforced(capsys: pytest.CaptureFixture[str]) -> None:
    for value in ("0", "101"):
        code, _, err = run_cli(capsys, "perceive", "--max-steps", value)
        assert code == 2
        assert err.strip()


def test_the_provider_flag_is_a_closed_set(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["perceive", "--provider", "gpt-5"])
    capsys.readouterr()


def test_doctor_reports_the_perception_block_without_printing_a_key(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-do-not-print-me")

    code, payload, _ = run_cli(capsys, "doctor")

    assert code == 0
    perception = payload["perception"]
    assert perception["provider"] == "fake"
    assert perception["paid"] is False
    assert perception["offline"] is True
    assert perception["api_key_env"] == "OPENAI_API_KEY"
    assert perception["api_key_present"] is True  # 只说在不在，不说是什么
    assert perception["budget_usd"] == 0.0
    assert perception["price_configured"] is False
    assert payload["client_capabilities_verified"] is False
    assert "sk-do-not-print-me" not in json.dumps(payload)


def test_perceive_never_dials_out(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*args: object, **kwargs: object) -> object:
        raise AssertionError(f"离线感知试图联网: args={args!r} kwargs={kwargs!r}")

    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(urllib.request, "urlopen", refuse)

    code, payload, _ = run_cli(capsys, "perceive", "--max-steps", "2")

    assert code == 0
    assert len(payload["scenes"]) == 2
    assert payload["provider"]["api_key_present"] is False
