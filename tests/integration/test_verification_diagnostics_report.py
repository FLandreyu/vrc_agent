"""诊断必须经真实离线 runtime 进入 JSON 报告事件，不能只在技能对象里可见。"""

import asyncio
import copy
import json

import pytest
from test_navigation_v6 import assembly

from vrc_agent.live_navigation.stability_report import validate_stability_report


def test_feedback_diagnostics_survive_report_serialization_and_validation():
    runtime, client, _ = assembly()
    original = runtime.derive

    def uncertain_scene(*args):
        observation, scene = original(*args)
        # 不伪造稳定性证明，仅注入验证输入；两项拒绝须原样保存到报告。
        return observation, scene.model_copy(
            update={
                "confidence": 0.6,
                "hazards": ("synthetic_hazard",),
                "uncertainties": ("synthetic_uncertainty",),
            }
        )

    # 本测试关注 verify -> event -> report；source/derived 审计不被改写。
    runtime.derive = uncertain_scene
    report = json.loads(json.dumps(asyncio.run(runtime.run())))
    assert report["outcome"] == "TARGET_UNOBSERVED"
    feedback = [e for e in report["events"] if e["event"] == "navigation_feedback"]
    assert [e["unknowns"] for e in feedback] == [1, 2, 3]
    for event in feedback:
        assert event["evidence"]["failed_predicates"] == [
            "scene_confidence_sufficient",
            "scene_no_hazards",
            "scene_no_uncertainties",
        ]
    validate_stability_report(report)
    bad = copy.deepcopy(report)
    event = next(e for e in bad["events"] if e["event"] == "navigation_feedback")
    event["evidence"]["failed_predicates"] = []
    with pytest.raises(ValueError, match="verification diagnostics"):
        validate_stability_report(bad)
    assert client.core.active is None
