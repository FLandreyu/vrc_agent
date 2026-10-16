"""全像素容差 + 全局面积上限 + 导航证据保护区；不设局部面积判据。

判据形态由现场复验决定（`docs/validation/T08_stability_v2_field.md` 第 6 节）：
真实渲染输出上，环境动画（客户端 UI、镜中角色、画面顶部动画带）在安静/活跃
姿态分别占保留区域 0.14–0.17% / 1.71–2.14%，而真实移动 ≥61.6%；v2 的「32×32
单块 ≤15」在最安静的窗口里也不可满足（单块实测 166–926/1024），且环境动画
本身可以形成同量级的连续变化区，任何局部面积阈值都无法同时容下环境动画与
拦住小移动体。因此 v3 只保留现场可分离的两个量：

- 全局面积上限 `max_changed_ppm`（5%）：对实测环境上限仍有 ≥2.3 倍余量，
  对最小移动信号（61.6%）有 ≥12 倍余量；
- 保护区零容差：导航证据实体 bbox 外扩 `protected_margin` 内必须一个变化
  像素都没有（安全相关的局部保护不变）。

比较器复用 v2 已审计的整数通道槽实现，不重复实现位运算；本模块不引入新依赖。

v4 沿用同一指标形态，并在策略里声明 `protected_mask_policy: reject_on_overlap`：
外扩保护矩形与遮罩相交时，遮罩会把交集里的变化清零，此时本模块拒绝（
`protected region masked`）而不是放行；运行时另在派生/执行前中止
`MASKED_SCENE_EVIDENCE`。未声明该字段的 v1–v3 保持既有已复核行为不变。
"""

import hashlib

from .stability import masked_protection_overlap, masked_rgb, rejects_masked_protection
from .stability_v2 import changed_flags, protected_rectangles


def verdict(metrics, policy):
    """由报告中的计数复验判据，不声称能用摘要重算像素。"""
    width, height = policy["frame_size"]
    retained = width * height - sum(
        (right - left) * (bottom - top)
        for left, top, right, bottom in policy["excluded_rectangles"]
    )
    if type(metrics.get("retained_pixels")) is not int or metrics["retained_pixels"] != retained:
        raise ValueError("invalid retained pixel count")
    changed = metrics.get("changed_pixels")
    if type(changed) is not int or not 0 <= changed <= retained:
        raise ValueError("invalid changed pixel count")
    if type(metrics.get("black")) is not bool:
        raise ValueError("invalid black verdict")
    # v3 没有逐块判据；证据里出现块计数键即与策略不符，按拒绝处理。
    if "max_tile_changed" in metrics:
        raise ValueError("unexpected tile comparison details")
    # v4 声明外扩保护矩形不得与遮罩相交；相交时遮罩会把保护区内变化清零，
    # 这时不能宣称「保护区零变化」，必须拒绝而不是当成通过（复核记录 F1）。
    if rejects_masked_protection(policy):
        rectangles = metrics.get("protected_rectangles")
        if not isinstance(rectangles, list) or any(
            not isinstance(rect, list)
            or len(rect) != 4
            or any(type(value) is not int for value in rect)
            for rect in rectangles
        ):
            raise ValueError("invalid protected evidence regions")
        if masked_protection_overlap(rectangles, policy):
            raise ValueError("protected region masked")
    if changed * 1_000_000 > retained * policy["max_changed_ppm"]:
        if metrics.get("protected_changed") is not None:
            raise ValueError("unexpected partial comparison details")
        return False
    protected = metrics.get("protected_changed")
    if type(protected) is not int or not 0 <= protected <= changed:
        raise ValueError("invalid protected change count")
    return not metrics["black"] and protected == 0


def black_frame(rgb, threshold):
    # 分块避免 max() 在整幅黑帧上长时间持有 GIL，控制轮询仍能调度。
    for start in range(0, len(rgb), 65536):
        if max(rgb[start : start + 65536]) > threshold:
            return False
    return True


def compare_v3(reference, current, policy, entities=()):
    a, b = masked_rgb(reference, policy), masked_rgb(current, policy)
    flags = changed_flags(a, b, policy["pixel_tolerance"])
    width, height = policy["frame_size"]
    retained = width * height - sum(
        (right - left) * (bottom - top)
        for left, top, right, bottom in policy["excluded_rectangles"]
    )
    changed = flags.count(b"\x80")
    rectangles = protected_rectangles(entities, policy)
    black = black_frame(a, policy["black_threshold"]) or black_frame(b, policy["black_threshold"])
    # 全局已超限时不再扫描逐像素位置：与 v2 一致，局部计数留空表示未评估。
    protected = None
    if changed * 1_000_000 <= retained * policy["max_changed_ppm"]:
        protected = 0
        if rectangles:
            index = flags.find(b"\x80")
            while index >= 0:
                y, x = divmod(index, width)
                protected += any(
                    left <= x < right and top <= y < bottom
                    for left, top, right, bottom in rectangles
                )
                index = flags.find(b"\x80", index + 1)
    result = dict(
        reference_digest=reference.digest(),
        current_digest=current.digest(),
        reference_rgb_digest=hashlib.sha256(a).hexdigest(),
        current_rgb_digest=hashlib.sha256(b).hexdigest(),
        black=black,
        changed_pixels=changed,
        retained_pixels=retained,
        protected_changed=protected,
        protected_rectangles=[list(r) for r in rectangles],
    )
    result["stable"] = verdict(result, policy)
    return result
