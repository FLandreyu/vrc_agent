"""方向 A 候选策略：遮去显式 HUD 区域后逐字节比较 RGB，不作语义安全保证。

策略独立于冻结世界；候选 HUD 区域尚待现场确认。任何变更必须发布新版本和摘要。
比较不降采样、不使用平均差，因此保留区域的一像素变化也会拒绝。
"""

import hashlib
import json
from importlib.resources import files

from vrc_agent.capture.frames import Frame


def load_policy():
    root = files("vrc_agent.live_navigation")
    raw = root.joinpath("stability_v1.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = root.joinpath("stability_v1.sha256").read_text(encoding="ascii").strip()
    if digest != expected:
        raise ValueError("stability policy digest mismatch")
    return json.loads(raw), digest


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


def compare(reference: Frame, current: Frame, policy: dict) -> dict:
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
