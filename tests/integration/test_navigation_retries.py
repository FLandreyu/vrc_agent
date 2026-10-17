"""各 attempt 的真实合成像素比较与共享预算；不调用外部模型。"""

import asyncio
import copy

import pytest
from test_navigation_v6 import assembly

from vrc_agent.capture.frames import Frame
from vrc_agent.live_navigation.stability_report import validate_stability_report


@pytest.mark.parametrize("checkpoint", [1, 2, 3])
@pytest.mark.parametrize("always", [False, True])
def test_bounded_retry_every_checkpoint(checkpoint, always):
    runtime, client, calls = assembly()
    phase = [0]
    proof_counts = {}
    original_proof, factory = runtime.fresh_proof, runtime.source_factory

    async def tracked(*args, **kwargs):
        key = (runtime.attempt_cycle, runtime.attempt)
        proof_counts[key] = proof_counts.get(key, 0) + 1
        phase[0] = proof_counts[key]
        try:
            return await original_proof(*args, **kwargs)
        finally:
            phase[0] = 0

    def source(authority):
        src = factory(authority)
        get_frame = src.frame_for

        def altered(obs):
            frame = get_frame(obs)
            if (
                phase[0] == checkpoint
                and (always or runtime.attempt == 1)
                and runtime.attempt_cycle == 1
            ):
                data = bytearray(frame.data)
                if checkpoint == 1:
                    data[:] = b"\x70\x70\x70\xff" * (frame.width * frame.height)
                pos = (900 * 2160 + 1000) * 4  # forward_clearance 可见保护区内，非遮罩。
                data[pos : pos + 3] = b"\x70\x70\x70"
                return Frame(
                    frame.ref, bytes(data), frame.width, frame.height, frame.captured_at_ns
                )
            return frame

        src.frame_for = altered
        return src

    runtime.fresh_proof, runtime.source_factory = tracked, source
    report = asyncio.run(runtime.run())
    validate_stability_report(report)
    starts = [
        e for e in report["events"] if e["event"] == "stability_attempt" and e["attempt_cycle"] == 1
    ]
    assert [e["attempt"] for e in starts] == ([1, 2, 3] if always else [1, 2])
    assert all(p["attempt"] <= 3 for p in report["stability_prechecks"])
    if always:
        assert report["outcome"] == "SCENE_UNSTABLE" and not report["actions"]
        assert len(calls) == (0 if checkpoint == 1 else 3)
        assert not client.transport.messages
    else:
        assert report["outcome"] == "SUCCESS"
        if checkpoint >= 2:
            assert (
                proof_counts[(1, 2)] == 3
            )  # 第2次预检、after_inference、before_submit均实际比较。
    bad = copy.deepcopy(report)
    bad["run_parameters"]["max_inference_attempts"] = 4
    with pytest.raises(ValueError):
        validate_stability_report(bad)
    bad = copy.deepcopy(report)
    bad["stability_prechecks"][0]["validates_observation_id"] = "fake"
    with pytest.raises(ValueError):
        validate_stability_report(bad)
