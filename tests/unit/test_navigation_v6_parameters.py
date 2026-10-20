"""v6参数闭集：旧版和像素判据不变。"""

import asyncio
import copy
import hashlib
from pathlib import Path

import pytest
from test_live_navigation import scene_for
from test_stability_v3 import entity, pair

from vrc_agent.config import AppConfig, load_config
from vrc_agent.contracts import MotionArgs
from vrc_agent.live_navigation.parameters import (
    action_freshness_ms,
    motion_profile,
    profile_declarations,
    validate_run_parameters,
)
from vrc_agent.live_navigation.skill import CalibratedSkill
from vrc_agent.live_navigation.stability import compare, load_policy
from vrc_agent.live_navigation.world import load_live_world


def config(profile="field", freshness=4000):
    return AppConfig(
        navigation_design="stable-inference-v6",
        navigation_motion_profile=profile,
        control={"observation_max_age_ms": freshness},
    )


def test_v6_digest_and_exact_budget_only_difference():
    v5, old_digest = load_policy("v5")
    v6, digest = load_policy("v6")
    assert old_digest == "2e21c734ec902c44b3111de8f6f62f06626fbbe9410ff9a0b959469248eb4faa"
    assert digest == "e46e0c2816c6f512fbd4fd5e5c60ff1ef3fc4d0560e0e8b29d3b6a6315caa513"
    assert {k for k in v5.keys() | v6.keys() if v5.get(k) != v6.get(k)} == {
        "version",
        "freshness_ms",
        "task_timeout_s",
        "motion_profiles",
    }
    assert v6["freshness_ms"] == 4000 and v6["task_timeout_s"] == 180.0 and v6["max_actions"] == 40
    assert v6["status"] == "FROZEN"
    for version in ("v1", "v2", "v3", "v4", "v5"):
        assert load_policy(version)[0]["status"] == (
            "FROZEN" if version == "v5" else "REQUIRES_FIELD_CALIBRATION"
        )
    assert v6["motion_profiles"] == profile_declarations()
    for change in ([], [(1450, 150, 90)], [(1540, 150, 90)]):
        frames = pair(changes=change)
        bbox = (1400 / 2160, 100 / 1440, 1520 / 2160, 200 / 1440)
        assert compare(*frames, v6, (entity(bbox),)) == compare(*frames, v5, (entity(bbox),))
    path = Path("src/vrc_agent/live_navigation/stability_v6.json")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest


def test_config_default_actual_and_minimum_freshness():
    default = AppConfig()
    assert (
        default.control.observation_max_age_ms == 1500
        and default.navigation_motion_profile == "cautious"
    )
    actual = load_config(Path("configs/navigation-live.toml"))
    assert actual.mode == "dry-run" and actual.navigation_design == "stable-inference-v10"
    assert (
        actual.navigation_motion_profile == "field"
        and actual.control.observation_max_age_ms == 4000
    )
    v6 = load_policy("v6")[0]
    assert action_freshness_ms(config(freshness=5000), v6) == 4000
    assert action_freshness_ms(config(freshness=3000), v6) == 3000
    assert action_freshness_ms(config(freshness=4000), load_policy("v5")[0]) == 1500


@pytest.mark.parametrize(
    "data",
    [
        {"control": {"observation_max_age_ms": 5001}},
        {"control": {"observation_max_age_ms": True}},
        {"control": {"observation_max_age_ms": 4000}},
        {"navigation_motion_profile": "field"},
        {"navigation_design": "stable-inference-v6", "navigation_motion_profile": "fast"},
        {
            "navigation_design": "stable-inference-v6",
            "navigation_motion_profile": "field",
            "control": {"max_motion_ms": 200},
        },
    ],
)
def test_invalid_or_legacy_extended_config_rejected(data):
    with pytest.raises(ValueError):
        AppConfig(**data)


@pytest.mark.parametrize("name,value,duration", [("cautious", 0.2, 200), ("field", 0.4, 500)])
def test_profile_proposals_and_clearance_remain_required(name, value, duration):
    skill = CalibratedSkill(load_live_world("v2").landmark("G2"), profile=name)
    scene = scene_for(skill, (0.41, 0.44, 0.57, 0.6))
    action = asyncio.run(skill.decide(scene, ()))
    assert action.args.forward == value and action.args.duration_ms == duration
    assert motion_profile(name).accepts(action.args)
    missing = scene_for(skill, (0.41, 0.44, 0.57, 0.6), floor=False)
    assert asyncio.run(skill.decide(missing, ())).kind == "wait"
    assert not skill.recovery_allowed(missing)


@pytest.mark.parametrize(
    "args",
    [
        {"forward": 0.2, "duration_ms": 500},
        {"forward": 0.4, "duration_ms": 200},
        {"forward": 0.5, "duration_ms": 500},
        {"forward": -0.4, "duration_ms": 500},
        {"forward": 0.4, "yaw": 1.0, "duration_ms": 500},
        {"yaw": 0.2, "duration_ms": 200},
        {"strafe": 0.4, "duration_ms": 500},
        {"duration_ms": 500},
    ],
)
def test_field_envelope_rejects_unselected_or_mixed_actions(args):
    assert not motion_profile("field").accepts(MotionArgs(**args))


@pytest.mark.parametrize(
    "fault", ["missing", "extra", "profile", "speed", "bool_yaw", "freshness", "timeout", "actions"]
)
def test_v6_report_parameter_integrity(fault):
    p = load_policy("v6")[0]
    values = dict(
        motion_profile="field",
        motion_envelope=copy.deepcopy(p["motion_profiles"]["field"]),
        freshness_ms=4000,
        task_timeout_s=180.0,
        max_actions=40,
    )
    assert validate_run_parameters(values, p)["freshness_ms"] == 4000
    if fault == "missing":
        del values["motion_envelope"]
    elif fault == "extra":
        values["untrusted"] = True
    elif fault == "profile":
        values["motion_profile"] = "cautious"
    elif fault == "speed":
        values["motion_envelope"]["forward"] = 1.0
    elif fault == "bool_yaw":
        values["motion_envelope"]["yaw"] = True
    elif fault == "freshness":
        values["freshness_ms"] = 5000
    elif fault == "timeout":
        values["task_timeout_s"] = 181.0
    else:
        values["max_actions"] = 41
    with pytest.raises(ValueError):
        validate_run_parameters(values, p)
