"""只用内存帧/模型：共享三次预算，每次重试完整预热与检查。"""

import asyncio
import copy

import pytest
from test_navigation_v6 import assembly

from vrc_agent.capture.frames import Frame
from vrc_agent.live_navigation.stability_report import validate_stability_report


@pytest.mark.parametrize("changed_at,expected_calls", [(4, 0), (5, 1), (6, 1)])
def test_precheck_and_both_original_checkpoints_reject(changed_at, expected_calls):
    runtime, client, calls = assembly()
    original = runtime.source_factory

    def factory(authority):
        source = original(authority)
        frame_for = source.frame_for

        def altered(obs):
            frame = frame_for(obs)
            if source.index >= changed_at:
                return Frame(
                    frame.ref, b"\x60\x60\x60\xff" * (2160 * 1440), 2160, 1440, frame.captured_at_ns
                )
            return frame

        source.frame_for = altered
        return source

    runtime.source_factory = factory
    report = asyncio.run(runtime.run())
    # 持久变化成为新参考后可以恢复；不再把首次拒绝当作必然终止。
    assert report["outcome"] == "SUCCESS"
    assert len(calls) >= expected_calls
    assert len(report["warmup_checks"]) >= 4
    assert report["run_parameters"]["max_inference_attempts"] == 3
    validate_stability_report(report)


def test_cost_report_recomputes_quantiles_and_preserves_checkpoints():
    runtime, _, _ = assembly()
    report = asyncio.run(runtime.run())
    validate_stability_report(report)
    assert report["stage_timings"]["infer"]["count"] == len(
        [e for e in report["events"] if e["event"] == "inference_source"]
    )
    assert report["stage_timings"]["infer"]["p50_ms"] == 3500
    assert report["stage_timings"]["submit"]["count"] == len(report["actions"])
    for edit in ("summary", "precheck", "checkpoint"):
        bad = copy.deepcopy(report)
        if edit == "summary":
            bad["stage_timings"]["infer"]["p95_ms"] += 1
        elif edit == "precheck":
            bad["stability_prechecks"] = []
        else:
            bad["stability_proofs"] = [
                p for p in bad["stability_proofs"] if "validates_observation_id" not in p
            ]
        with pytest.raises(ValueError):
            validate_stability_report(bad)
