"""方向 A 候选策略：遮去显式 HUD 区域后逐字节比较 RGB，不作语义安全保证。

策略独立于冻结世界；候选 HUD 区域尚待现场确认。任何变更必须发布新版本和摘要。
比较不降采样、不使用平均差，因此保留区域的一像素变化也会拒绝。
"""

import hashlib
import json
from importlib.resources import files

from vrc_agent.capture.frames import Frame


def load_policy(version="v1"):
    if version not in {"v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"}:
        raise ValueError("unknown stability policy version")
    root = files("vrc_agent.live_navigation")
    raw = root.joinpath(f"stability_{version}.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = root.joinpath(f"stability_{version}.sha256").read_text(encoding="ascii").strip()
    if digest != expected:
        raise ValueError("stability policy digest mismatch")
    policy = json.loads(raw)
    if version == "v8":
        from .acceptance_v8 import DECLARATION

        expected_v8 = {
            **load_policy("v6")[0],
            "version": "local-rgb-stability-v8-candidate",
            "status": policy.get("status"),
            **DECLARATION,
        }
        if (
            type(policy.get("status")) is not str
            or policy["status"] not in {"REQUIRES_FIELD_CALIBRATION", "FROZEN"}
            or json.dumps(policy, sort_keys=True, allow_nan=False)
            != json.dumps(expected_v8, sort_keys=True)
        ):
            raise ValueError("invalid v8 acceptance candidate declaration")
    if version == "v7":
        expected_v7 = {
            **load_policy("v6")[0],
            "version": "local-rgb-stability-v7-candidate",
            "status": "REQUIRES_FIELD_CALIBRATION",
            "zero_tolerance_labels": ["home_raccoon"],
            "evidence_changed_ppm": 1000,
        }
        if json.dumps(policy, sort_keys=True, allow_nan=False) != json.dumps(
            expected_v7, sort_keys=True
        ):
            raise ValueError("invalid v7 candidate declaration")
    if version == "v6":
        from .parameters import profile_declarations

        expected = {
            **load_policy("v5")[0],
            "version": "local-rgb-stability-v6-candidate",
            "status": policy.get("status"),
            "freshness_ms": 4000,
            "task_timeout_s": 180.0,
            "motion_profiles": profile_declarations(),
        }
        if (
            type(policy.get("status")) is not str
            or policy["status"] not in {"REQUIRES_FIELD_CALIBRATION", "FROZEN"}
            or json.dumps(policy, sort_keys=True, allow_nan=False)
            != json.dumps(expected, sort_keys=True)
        ):
            raise ValueError("invalid v6 budget policy or changed stability criteria")
    return policy, digest


def masked_rgb(frame: Frame, policy: dict) -> bytes:
    if (
        list(frame.size) != policy["frame_size"]
        or len(frame.data) != frame.width * frame.height * 4
    ):
        raise ValueError("stability frame mismatch")
    # 切片循环在 worker 中调用；RGB 保留所有像素，忽略没有视觉含义的 alpha。
    rgb = bytearray(len(frame.data) // 4 * 3)
    for channel in range(3):
        rgb[channel::3] = frame.data[channel::4]
    for x1, y1, x2, y2 in policy["excluded_rectangles"]:
        for y in range(y1, y2):
            start, end = (y * frame.width + x1) * 3, (y * frame.width + x2) * 3
            rgb[start:end] = b"\0" * (end - start)
    return bytes(rgb)


def compare(reference: Frame, current: Frame, policy: dict, entities=()) -> dict:
    if policy.get("version") == "local-rgb-stability-v7-candidate":
        from .stability_v7 import compare_v7

        return compare_v7(reference, current, policy, entities)
    if policy["metric"] == "rgb_tolerance_visible_guard_v5":
        from .stability_v5 import compare_v5

        return compare_v5(reference, current, policy, entities)
    if policy["metric"] == "rgb_tolerance_local_guard_v2":
        from .stability_v2 import compare_v2

        return compare_v2(reference, current, policy, entities)
    if policy["metric"] == "rgb_tolerance_area_guard_v3":
        from .stability_v3 import compare_v3

        return compare_v3(reference, current, policy, entities)
    a, b = masked_rgb(reference, policy), masked_rgb(current, policy)

    # 分块避免 max() 在整幅黑帧上长时间持有 GIL，控制轮询仍能调度。
    def black_frame(data):
        for start in range(0, len(data), 65536):
            chunk = data[start : start + 65536]
            if max(chunk) > 8:
                return False
        return True

    black = black_frame(a) or black_frame(b)
    return {
        "reference_digest": reference.digest(),
        "current_digest": current.digest(),
        "reference_rgb_digest": hashlib.sha256(a).hexdigest(),
        "current_rgb_digest": hashlib.sha256(b).hexdigest(),
        "black": black,
        "equal": a == b and not black,
    }


def intersects_mask(entity, policy):
    w, h = policy["frame_size"]
    x1, y1, x2, y2 = entity.bbox
    return any(
        x1 * w < right and x2 * w > left and y1 * h < bottom and y2 * h > top
        for left, top, right, bottom in policy["excluded_rectangles"]
    )


def rejects_masked_protection(policy):
    """策略是否声明「外扩保护矩形不得与遮罩相交」（v4 起，见复核记录 F1）。"""
    return policy.get("protected_mask_policy") == "reject_on_overlap"


def masked_protection_overlap(rectangles, policy):
    """像素坐标下的保护矩形是否与排除矩形相交；相交时遮罩会把保护区内变化清零。

    该集合比较不依赖实体，只由策略 `excluded_rectangles` 与调用方传入的
    `protected_rectangles` 决定，因此比较器、运行时与报告复验共用同一判据。
    """
    return any(
        left < mask_right and right > mask_left and top < mask_bottom and bottom > mask_top
        for left, top, right, bottom in rectangles
        for mask_left, mask_top, mask_right, mask_bottom in policy["excluded_rectangles"]
    )
