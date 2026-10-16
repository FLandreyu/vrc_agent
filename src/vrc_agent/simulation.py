"""合成导航回放装配，与不具控制权限的 planner 包分离；无网络。"""

from __future__ import annotations

import json
from importlib.resources import files

from vrc_agent.capture.errors import ReplayExhausted
from vrc_agent.capture.replay import ReplaySource, collect, parse_fixture
from vrc_agent.clock import FakeClock
from vrc_agent.control.replay import ReplayController
from vrc_agent.perception.interpreter import Perception
from vrc_agent.planner import LandmarkPlanner, NavigationGoal
from vrc_agent.providers.fake import FakeSceneProvider
from vrc_agent.runtime import Runtime

SCENARIOS = ("navigation", "no_progress", "model_late", "estop")


def load_scenario(name: str) -> dict:
    if name not in SCENARIOS:
        raise ValueError("unknown offline scenario")
    return json.loads(files("vrc_agent.planner").joinpath(f"fixtures/{name}.json").read_text())


class ScheduledSource:
    """保留 T04 原始捕获时间，不用执行/推理结束时刻给旧帧重新打时间戳。"""

    def __init__(self, records, controller: ReplayController):
        self.records = records
        self.controller = controller
        self.index = 0

    def next_observation(self):
        if self.index >= len(self.records):
            raise ReplayExhausted("T06 fixture exhausted")
        observation, _ = self.records[self.index]
        self.index += 1
        self.controller.advance_to(observation.captured_at_ns)
        return observation

    def frame_for(self, observation):
        for candidate, frame in self.records:
            if candidate.observation_id == observation.observation_id:
                return frame
        return None

    def close(self):
        self.records.clear()


class SyntheticVision(FakeSceneProvider):
    """仅识别合成帧中的纯绿色矩形；不读取 fixture 的目标状态或验证结论。"""

    def __init__(self, controller, delays_ms, *, label="synthetic_target", color=(0, 255, 0)):
        super().__init__(clock=controller.clock)
        self.controller = controller
        self.delays = iter(delays_ms)
        self.label, self.color = label, bytes(color)

    async def infer(self, model_input, *, deadline, cancel):
        delay = next(self.delays, 0)
        self.controller.advance_to(self.controller.clock.now_ns() + delay * 1_000_000)
        # 故意模拟不遵守 deadline 的供应商；Runtime 必须独立拒绝迟到结果。
        from vrc_agent.providers.base import Deadline

        return await super().infer(model_input, deadline=Deadline.unlimited(), cancel=cancel)

    def payload_for(self, model_input):
        width, height = model_input.image_size
        raw = model_input.image or b""
        pixels = [i // 4 for i in range(0, len(raw), 4) if raw[i : i + 3] == self.color]
        if not pixels:
            return {"entities": [], "uncertainties": ["synthetic target not visible"]}
        xs, ys = [p % width for p in pixels], [p // width for p in pixels]
        return {
            "entities": [
                {
                    "track_id": "synthetic-target",
                    "label": self.label,
                    "bbox": [
                        min(xs) / width,
                        min(ys) / height,
                        (max(xs) + 1) / width,
                        (max(ys) + 1) / height,
                    ],
                    "confidence": 1.0,
                    "evidence_observation_id": model_input.observation_id,
                }
            ],
            "hazards": [],
            "uncertainties": [],
        }


def build_simulation(data: dict, *, planner=None):
    fixture = parse_fixture(data["capture"])
    if len(fixture.steps) > 100:
        raise ValueError("offline fixture limited to 100 observations")
    replay = ReplaySource(fixture)
    try:
        records = collect(replay)
    finally:
        replay.close()
    clock = FakeClock(fixture.start_ns)
    stop_ms = data.get("estop_at_ms")
    controller = ReplayController(
        fixture.session_id,
        clock,
        estop_at_ns=None if stop_ms is None else fixture.start_ns + stop_ms * 1_000_000,
    )
    controller.arm()  # 只存在于这个明确的离线装配入口。
    goal = NavigationGoal.model_validate(data.get("goal", {}))
    source = ScheduledSource(records, controller)
    perception = Perception(
        SyntheticVision(controller, data.get("model_delays_ms", [])), clock=clock
    )
    runtime = Runtime(
        source,
        perception,
        planner or LandmarkPlanner(goal),
        controller,
        goal,
        max_actions=data.get("max_actions", 8),
    )
    return runtime


async def run_scenario(name: str):
    return await build_simulation(load_scenario(name)).run()
