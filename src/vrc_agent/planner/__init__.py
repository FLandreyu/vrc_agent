"""短动作规划与结果验证；此模块不访问控制器或输入传输。"""

from typing import Annotated, Protocol

from pydantic import Field

from vrc_agent.contracts import (
    ActionProposal,
    ActionRequest,
    ActionResult,
    Contract,
    Entity,
    Observation,
    SceneState,
)


class NavigationGoal(Contract):
    """可信配置的图像空间目标；面积是模拟判据，不是世界距离。"""

    label: Annotated[str, Field(min_length=1, max_length=64)] = "synthetic_target"
    min_confidence: Annotated[float, Field(ge=0.5, le=1)] = 0.8
    arrival_area: Annotated[float, Field(gt=0, le=1)] = 0.35
    progress_epsilon: Annotated[float, Field(gt=0, le=1)] = 0.01


def target(scene: SceneState, goal: NavigationGoal) -> Entity | None:
    if (
        scene.confidence is None
        or scene.confidence < goal.min_confidence
        or scene.hazards
        or scene.uncertainties
    ):
        return None
    matches = [
        entity
        for entity in scene.entities
        if entity.label == goal.label
        and entity.confidence >= goal.min_confidence
        and entity.evidence_observation_id in scene.observation_ids
    ]
    return matches[0] if len(matches) == 1 else None


def area(entity: Entity) -> float:
    x1, y1, x2, y2 = entity.bbox
    return (x2 - x1) * (y2 - y1)


class Planner(Protocol):
    async def decide(
        self, scene: SceneState, recent_results: tuple[ActionResult, ...]
    ) -> ActionProposal: ...


class LandmarkPlanner:
    """保守的确定性策略；unknown 等待，不执行模型文本中的指令。"""

    def __init__(self, goal: NavigationGoal):
        self.goal = goal

    async def decide(
        self, scene: SceneState, recent_results: tuple[ActionResult, ...]
    ) -> ActionProposal:
        if recent_results and recent_results[-1].status != "COMPLETED":
            return ActionProposal(kind="stop", args={}, expected_outcome="stop after interruption")
        entity = target(scene, self.goal)
        if entity is None or area(entity) >= self.goal.arrival_area:
            return ActionProposal(kind="wait", args={"duration_ms": 100})
        centre = (entity.bbox[0] + entity.bbox[2]) / 2
        args = {"duration_ms": 200}
        if abs(centre - 0.5) > 0.15:
            args["yaw"] = 0.2 if centre > 0.5 else -0.2
        else:
            args["forward"] = 0.2
        return ActionProposal(
            kind="motion", args=args, expected_outcome="observe target progress in a new frame"
        )


class ResultVerifier:
    """只采信动作结束后的同会话/epoch 新观测，绝不解析 expected_outcome。"""

    def __init__(self, goal: NavigationGoal):
        self.goal = goal

    def verify(
        self,
        action: ActionRequest,
        result: ActionResult,
        before: SceneState,
        observation: Observation,
        after: SceneState,
    ) -> ActionResult:
        valid = (
            result.status == "COMPLETED"
            and result.action_id == action.action_id
            and result.session_id == action.session_id
            and action.kind == "motion"
            and result.finished_at_ns is not None
            and observation.captured_at_ns > result.finished_at_ns
            and observation.observation_id != action.based_on_observation_id
            and observation.window_state == "visible"
            and observation.frame_ref is not None
            and observation.session_id == after.session_id == before.session_id == action.session_id
            and observation.environment_epoch
            == after.environment_epoch
            == before.environment_epoch
            == action.environment_epoch
            and after.observation_ids == (observation.observation_id,)
            and after.generated_at_ns >= observation.captured_at_ns
            and action.based_on_observation_id in before.observation_ids
        )
        current = target(after, self.goal) if valid else None
        previous = target(before, self.goal) if valid else None
        verification, reason = "UNKNOWN", result.reason_code
        evidence = (action.based_on_observation_id,)
        if current and current.last_seen_ns == observation.captured_at_ns:
            evidence += (observation.observation_id,)
            if area(current) >= self.goal.arrival_area:
                verification = "SUCCESS"
            elif previous and area(current) <= area(previous) + self.goal.progress_epsilon:
                verification, reason = "FAILURE", "NO_PROGRESS"
        return ActionResult.model_validate(
            {
                **result.model_dump(),
                "verification": verification,
                "reason_code": reason,
                "evidence_observation_ids": evidence,
            }
        )
