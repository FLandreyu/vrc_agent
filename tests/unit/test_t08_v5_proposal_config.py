import copy
import json
from pathlib import Path

import pytest

from vrc_agent.config import load_config
from vrc_agent.live_navigation.world import load_live_world
from vrc_agent.live_navigation.world_proposal import WorldV2Proposal, load_world_proposal
from vrc_agent.providers import factory


def test_actual_live_config_disables_thinking_through_provider_factory(monkeypatch):
    config = load_config(Path(__file__).parents[2] / "configs/navigation-live.toml")
    assert config.mode == "dry-run" and config.navigation_design == "stable-inference-v6"
    assert config.perception.disable_thinking and config.perception.max_output_tokens == 1024
    captured = []
    monkeypatch.setattr(factory, "OpenAICompatibleProvider", lambda **kw: captured.append(kw))
    factory.build_provider(config.perception, environ={config.perception.api_key_env: "synthetic"})
    assert captured[0]["disable_thinking"] is True  # 不连接服务，不读取真实密钥。


def proposal_data():
    world = load_live_world()
    return dict(
        version="vrchat-home-live-v2-proposal",
        status="PROPOSAL_NOT_APPROVED",
        source_world_digest=world.digest(),
        changes=[
            dict(
                group=g,
                arrival={**world.landmark(g).arrival.model_dump(), **values},
                prompt_addendum="Draft only",
                reference_sha256=None,
            )
            for g, values in [("G3", {"min_width": 0.62, "min_height": 0.65}), ("G5", {})]
        ],
    )


def test_explicit_proposal_loader_and_offline_size_evaluation(tmp_path):
    before = load_live_world().digest()
    path = tmp_path / "draft.json"
    path.write_text(json.dumps(proposal_data()), encoding="utf-8")
    draft = load_world_proposal(path)
    # 实测宽高 0.654x0.685 的居中合成框满足建议尺寸，不代表激活前现场已到达。
    assert draft.evaluate_box("G3", (0.173, 0.1575, 0.827, 0.8425))
    assert not draft.evaluate_box("G3", (0.2, 0.2, 0.8, 0.8))
    assert load_live_world().landmark("G3").arrival.min_width == 0.7
    assert load_live_world().digest() == before
    with pytest.raises(ValueError, match="outside proposal"):
        draft.evaluate_box("G1", (0.1, 0.1, 0.9, 0.9))


@pytest.mark.parametrize("mutation", ["approved", "hash", "source", "group"])
def test_proposal_cannot_claim_frozen_or_missing_field_evidence(mutation):
    data = copy.deepcopy(proposal_data())
    if mutation == "approved":
        data["status"] = "FROZEN"
    elif mutation == "hash":
        data["changes"][0]["reference_sha256"] = "0" * 64
    elif mutation == "source":
        data["source_world_digest"] = "0" * 64
    else:
        data["changes"][0]["group"] = "G1"
    with pytest.raises(ValueError):
        WorldV2Proposal.model_validate_json(json.dumps(data))
