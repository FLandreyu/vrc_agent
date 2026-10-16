import math

import pytest
from pydantic import ValidationError

from vrc_agent.config import AppConfig, AvatarParameter, load_config
from vrc_agent.contracts import ActionProposal, Entity


@pytest.mark.parametrize("value", ["0.5", True, 1.1, -1.1, math.nan, math.inf])
def test_motion_rejects_invalid_axes(value):
    with pytest.raises(ValidationError):
        ActionProposal.model_validate(
            {"kind": "motion", "args": {"forward": value, "duration_ms": 200}}
        )


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "shell", "args": {}},
        {"kind": "motion", "args": {"duration_ms": 501}},
        {"kind": "motion", "args": {"duration_ms": "200"}},
        {"kind": "motion", "args": {"duration_ms": 200, "command": "secret"}},
        {"kind": "stop", "args": {}, "lease_id": "forged"},
        {"kind": "stop", "args": {"duration_ms": 200}},
    ],
)
def test_model_cannot_expand_capabilities(data):
    with pytest.raises(ValidationError):
        ActionProposal.model_validate(data)


def test_contract_roundtrip():
    proposal = ActionProposal.model_validate({"kind": "motion", "args": {"duration_ms": 200}})
    assert ActionProposal.model_validate_json(proposal.model_dump_json()) == proposal


@pytest.mark.parametrize("text", ["a" * 145, "😀" * 73, "a\x00b", "\n" * 9])
def test_chat_boundaries(text):
    with pytest.raises(ValidationError):
        ActionProposal.model_validate({"kind": "chatbox", "args": {"text": text}})


def test_config_defaults_and_secrets(tmp_path):
    assert load_config().mode == "dry-run"
    path = tmp_path / "bad.toml"
    path.write_text('api_key = "not-a-real-secret"\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_config(path)
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"osc": {"host": "192.168.1.2"}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"control": {"heartbeat_timeout_ms": 100}})


def test_avatar_exact_types_and_bounds():
    spec = AvatarParameter(type="int", reset_value=0, minimum=0.0, maximum=3.0)
    assert spec.accepts(2)
    assert not spec.accepts(True)
    assert not spec.accepts(2.0)
    assert not spec.accepts(4)
    assert not AvatarParameter(type="int", reset_value=0).accepts(2**31)
    assert not AvatarParameter(type="float", reset_value=0.0).accepts(1e100)


@pytest.mark.parametrize("bbox", [(0.5, 0.1, 0.2, 0.8), (-0.1, 0.1, 0.2, 0.8)])
def test_scene_boxes_are_not_world_coordinates(bbox):
    with pytest.raises(ValidationError):
        Entity(
            track_id="track",
            label="door",
            bbox=bbox,
            confidence=0.7,
            evidence_observation_id="observation",
            last_seen_ns=1,
        )
