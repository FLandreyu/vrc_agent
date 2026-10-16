"""T08 专用图像空间试验台。真实输入不可达；积分记录型输入后渲染新帧。

标志大小/偏心不是米制距离或地图；故障仅改变环境，不直接写成功结论。
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from vrc_agent.capture.frames import Frame
from vrc_agent.capture.synthetic import render_bgra
from vrc_agent.clock import FakeClock
from vrc_agent.contracts import Observation
from vrc_agent.control.replay import ReplayController
from vrc_agent.perception.interpreter import Perception
from vrc_agent.runtime import Runtime
from vrc_agent.simulation import SyntheticVision
from vrc_agent.skills import NavigationSkill, load_first_world
from vrc_agent.skills.world import NavigationTask, WorldProfile


def digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class ImageWorldSource:
    """只给 Runtime 观测和内存帧；规划器拿不到模拟状态。"""

    def __init__(self, controller: ReplayController, task: NavigationTask, color):
        self.controller, self.task, self.color = controller, task, color
        self.centre, self.size = task.initial_centre, task.initial_size
        self.blocked = task.fault in {"blocked", "temporary_block"}
        self.side_travel = 0.0
        self.axes = {"/input/Vertical": 0.0, "/input/Horizontal": 0.0, "/input/LookHorizontal": 0.0}
        self.cursor, self.index = 0, 0
        self.last_ns = controller.clock.now_ns()
        self.frame = None

    def _integrate(self, until_ns):
        seconds = (until_ns - self.last_ns) / 1_000_000_000
        self.side_travel += abs(self.axes["/input/Horizontal"]) * seconds
        if self.task.fault == "temporary_block" and self.side_travel >= 0.035:
            self.blocked = False
        if not self.blocked:
            self.centre -= self.axes["/input/LookHorizontal"] * seconds * 1.5
            self.size += self.axes["/input/Vertical"] * seconds * 2.5
            self.size = max(0.1, min(0.8, self.size))
        self.last_ns = until_ns

    def next_observation(self):
        # 即使 wait，也必须捕获严格晚于动作结束的新帧；没有输入则图像不会自动接近。
        self.controller.advance_to(self.controller.clock.now_ns() + 20_000_000)
        now = self.controller.clock.now_ns()
        for message in self.controller.messages[self.cursor :]:
            self._integrate(message["at_ns"])
            if message["address"] in self.axes:
                self.axes[message["address"]] = message["values"][0]
        self.cursor = len(self.controller.messages)
        self._integrate(now)
        self.index += 1
        pattern = {"type": "solid", "bgr": [24, 24, 24]}
        if self.task.fault != "missing":
            half = self.size / 2
            # 视野裁剪；绝不把看不见的标志强制放回中心。
            left, right = max(0.0, self.centre - half), min(1.0, self.centre + half)
            if left < right:
                pattern = {
                    "type": "scene",
                    "background_bgr": [24, 24, 24],
                    "boxes": [
                        {"bbox": [left, 0.5 - half, right, 0.5 + half], "bgr": list(self.color)}
                    ],
                }
        observation = Observation(
            event_id=f"capture-{self.index:04d}",
            session_id=self.controller.session_id,
            observation_id=f"nav-obs-{self.index:04d}",
            environment_epoch=1,
            captured_at_ns=now,
            window_state="visible",
            frame_ref=f"nav-frame-{self.index:04d}",
            frame_size=(100, 100),
        )
        self.frame = Frame(observation.frame_ref, render_bgra(100, 100, pattern), 100, 100, now)
        return observation

    def frame_for(self, observation):
        return self.frame if self.frame and self.frame.ref == observation.frame_ref else None

    def close(self):
        self.frame = None


def build_navigation(world: WorldProfile, task: NavigationTask) -> Runtime:
    if task not in world.tasks:
        raise ValueError("task must belong to the frozen world suite")
    landmark = world.landmark(task.landmark_id)
    clock = FakeClock(1_000_000_000)
    controller = ReplayController(
        f"t08-{task.task_id}",
        clock,
        estop_at_ns=1_100_000_000 if task.fault == "takeover" else None,
    )
    controller.arm()  # 仅离线记录型控制器，不连接 live，不在失败后重启。
    skill = NavigationSkill(landmark.goal, world.limits)
    return Runtime(
        ImageWorldSource(controller, task, landmark.color_bgr),
        Perception(
            SyntheticVision(
                controller,
                [800] if task.fault == "late" else [],
                label=landmark.goal.label,
                color=landmark.color_bgr,
            ),
            clock=clock,
        ),
        skill,
        controller,
        landmark.goal,
        max_actions=world.limits.max_actions,
        navigation=skill,
    )


def classify(report: dict) -> str:
    outcome = report["outcome"]
    # 接管/急停永远失败，不能因此前某帧曾到达而覆盖。
    if outcome in {"MANUAL_STOP", "PREEMPTED"} or report["final_state"] == "ESTOP":
        return "TAKEOVER"
    if outcome == "SUCCESS":
        return (
            "NONE"
            if report["results"] and report["results"][-1]["verification"] == "SUCCESS"
            else "UNVERIFIED"
        )
    return {
        "RECOVERY_EXHAUSTED": "STUCK",
        "TARGET_UNOBSERVED": "TARGET_UNOBSERVED",
        "ACTION_LIMIT": "ACTION_BUDGET",
        "MODEL_LATE": "MODEL_TIMEOUT",
        "FOCUS_LOST": "OBSERVATION_INVALID",
        "STALE_OBSERVATION": "OBSERVATION_INVALID",
        "INVALID_SCENE_EVIDENCE": "OBSERVATION_INVALID",
        "MISSING_FRAME": "OBSERVATION_INVALID",
        "SESSION_MISMATCH": "AUTHORITY_LOST",
        "EPOCH_MISMATCH": "AUTHORITY_LOST",
        "EXPIRED_LEASE": "AUTHORITY_LOST",
        "NOT_ARMED": "AUTHORITY_LOST",
        "TRANSPORT_ERROR": "CONTROL_FAILED",
    }.get(outcome, "RUNTIME_FAILED")


async def run_navigation_suite() -> dict:
    world = load_first_world()
    trials = []
    for task in world.tasks:
        runtime = build_navigation(world, task)
        report = await runtime.run()
        category = classify(report)
        trials.append(
            {
                "task_id": task.task_id,
                "landmark_id": task.landmark_id,
                "start_view": task.start_view,
                "injected_fault": task.fault,
                "success": category == "NONE",
                "failure_class": category,
                "recoveries": runtime.navigation.recoveries,
                "trace": report,
            }
        )
    successes = sum(trial["success"] for trial in trials)
    result = {
        "schema_version": 1,
        "mode": "offline-simulation",
        "sends_live_input": False,
        "world_id": world.world_id,
        "suite_digest": digest(world.model_dump(mode="json")),
        "total": 20,
        "successes": successes,
        "success_rate": successes / 20,
        "offline_threshold_met": successes >= 16,
        "live_acceptance": "UNTESTED",
        "failure_counts": dict(
            sorted(Counter(t["failure_class"] for t in trials if not t["success"]).items())
        ),
        "trials": trials,
    }
    result["sequence_digest"] = digest(result)
    return result
