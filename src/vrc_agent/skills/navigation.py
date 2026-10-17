"""视觉短动作与有限恢复。只产生 Proposal；执行权始终由 Runtime 绑定。"""

from collections import deque

from vrc_agent.contracts import ActionProposal, ActionRequest, ActionResult, Observation, SceneState
from vrc_agent.planner import (
    NavigationGoal,
    area,
    target,
    target_candidates,
    target_predicates,
)

from .world import NavigationLimits

VERIFICATION_PREDICATES = frozenset(
    (
        "result_completed",
        "result_ok",
        "result_action_matches",
        "result_session_matches",
        "action_verifiable",
        "result_finished",
        "observation_after_finish",
        "capture_monotonic",
        "observation_unseen",
        "observation_new",
        "window_visible",
        "frame_bound",
        "session_consistent",
        "epoch_consistent",
        "scene_observation_bound",
        "scene_generated_after_capture",
        "action_before_bound",
        "scene_before_issue",
        "scene_confidence_known",
        "scene_confidence_sufficient",
        "scene_no_hazards",
        "scene_no_uncertainties",
        "unique_bound_confident_target",
        "target_timestamp_current",
    )
)


def offset(entity) -> float:
    return (entity.bbox[0] + entity.bbox[2]) / 2 - 0.5


class NavigationSkill:
    def __init__(self, goal: NavigationGoal, limits: NavigationLimits):
        self.goal, self.limits = goal, limits
        self.stalls = self.unknowns = self.recoveries = 0
        self.halt_reason: str | None = None
        self.arrival_evidence: list[str] = []
        self._arrival_context = None
        self.last_evidence: dict = {}
        self._last_capture = -1
        self._seen: set[str] = set()
        self._recovery: deque[str] = deque()
        self.last_skill = "wait"

    def target_size(self, entity) -> float:
        return area(entity)

    def arrived(self, entity) -> bool:
        return (
            self.target_size(entity) >= self.goal.arrival_area
            and abs(offset(entity)) <= self.limits.centre_tolerance
        )

    def recovery_allowed(self, scene: SceneState) -> bool:
        return True  # 离线合成空地；live 子类必须要求当帧正面安全证据。

    async def decide(self, scene: SceneState, recent_results: tuple[ActionResult, ...]):
        if self.halt_reason or (recent_results and recent_results[-1].status != "COMPLETED"):
            self.last_skill = "stop"
            return ActionProposal(kind="stop", args={})
        entity = target(scene, self.goal)
        # 恢复也不能消耗一次未知观测后盲动；队列停在原处，未知预算照常消耗。
        if entity is None:
            self.last_skill = "wait"
            return ActionProposal(kind="wait", args={"duration_ms": 100})
        if self._recovery:
            if self._recovery[0] == "recover_strafe" and not self.recovery_allowed(scene):
                self.halt_reason = "RECOVERY_UNSAFE"
                return ActionProposal(kind="stop", args={})
            self.last_skill = self._recovery.popleft()
            if self.last_skill == "recover_strafe":
                return ActionProposal(
                    kind="motion",
                    args={"strafe": 0.2 if self.recoveries % 2 else -0.2, "duration_ms": 200},
                )
            return ActionProposal(kind="wait", args={"duration_ms": 100})
        if abs(offset(entity)) > self.limits.centre_tolerance:
            self.last_skill = "turn"
            return ActionProposal(
                kind="motion",
                args={"yaw": 0.2 if offset(entity) > 0 else -0.2, "duration_ms": 200},
            )
        if self.arrived(entity):
            self.last_skill = "confirm"
            return ActionProposal(kind="wait", args={"duration_ms": 100})
        self.last_skill = "approach"
        return ActionProposal(kind="motion", args={"forward": 0.2, "duration_ms": 200})

    def verify(
        self,
        action: ActionRequest,
        result: ActionResult,
        before: SceneState,
        observation: Observation,
        after: SceneState,
    ) -> ActionResult:
        predicates = {
            "result_completed": result.status == "COMPLETED",
            "result_ok": result.reason_code == "OK",
            "result_action_matches": result.action_id == action.action_id,
            "result_session_matches": result.session_id == action.session_id,
            "action_verifiable": action.kind in {"motion", "wait"},
            "result_finished": result.finished_at_ns is not None,
            "observation_after_finish": result.finished_at_ns is not None
            and observation.captured_at_ns > result.finished_at_ns,
            "capture_monotonic": observation.captured_at_ns > self._last_capture,
            "observation_unseen": observation.observation_id not in self._seen,
            "observation_new": observation.observation_id != action.based_on_observation_id,
            "window_visible": observation.window_state == "visible",
            "frame_bound": observation.frame_ref is not None,
            "session_consistent": observation.session_id
            == after.session_id
            == before.session_id
            == action.session_id,
            "epoch_consistent": observation.environment_epoch
            == after.environment_epoch
            == before.environment_epoch
            == action.environment_epoch,
            "scene_observation_bound": after.observation_ids == (observation.observation_id,),
            "scene_generated_after_capture": after.generated_at_ns >= observation.captured_at_ns,
            "action_before_bound": action.based_on_observation_id in before.observation_ids,
            "scene_before_issue": before.generated_at_ns <= action.issued_at_ns,
        }
        valid = all(predicates.values())
        current = target(after, self.goal) if valid else None
        previous = target(before, self.goal) if valid else None
        if current and current.last_seen_ns != observation.captured_at_ns:
            current = None
        predicates.update(target_predicates(after, self.goal))
        candidates = target_candidates(after, self.goal)
        predicates["target_timestamp_current"] = (
            len(candidates) == 1 and candidates[0].last_seen_ns == observation.captured_at_ns
        )
        self.last_evidence = {
            "diagnostics_version": 1,
            "verification_predicates": predicates,
            "failed_predicates": [name for name, passed in predicates.items() if not passed],
            "observation_id": observation.observation_id,
            "valid_target": current is not None,
            "target_area": area(current) if current else None,
            "centre_error": abs(offset(current)) if current else None,
            "track_id": current.track_id if current else None,
        }
        evidence = (action.based_on_observation_id,)
        verification, reason = "UNKNOWN", result.reason_code
        if valid:
            self._seen.add(observation.observation_id)
            self._last_capture = observation.captured_at_ns
            evidence += (observation.observation_id,)
        if current is None:
            self.arrival_evidence.clear()
            self._arrival_context = None
            self.unknowns += 1
            self.stalls = 0
            if self.unknowns >= self.limits.unknown_limit:
                self.halt_reason = "TARGET_UNOBSERVED"
        else:
            self.unknowns = 0
            near = self.arrived(current)
            if near:
                context = (after.session_id, after.environment_epoch, current.track_id)
                if self._arrival_context != context or (
                    self.arrival_evidence and before.observation_ids != (self.arrival_evidence[-1],)
                ):
                    self.arrival_evidence.clear()
                self._arrival_context = context
                self.arrival_evidence.append(observation.observation_id)
                if len(self.arrival_evidence) >= self.limits.arrival_frames:
                    verification = "SUCCESS"
                    evidence = tuple(self.arrival_evidence)
            else:
                self.arrival_evidence.clear()
                self._arrival_context = None
            # 只比较同一目标；侧移恢复不计作前进，也不重置全任务恢复预算。
            progress = None
            if previous and previous.track_id == current.track_id and not near:
                if action.kind == "motion" and self.last_skill == "turn" and action.args.yaw:
                    progress = (
                        abs(offset(previous)) - abs(offset(current)) > self.limits.turn_progress
                    )
                elif (
                    action.kind == "motion"
                    and self.last_skill == "approach"
                    and action.args.forward > 0
                ):
                    progress = (
                        self.target_size(current) - self.target_size(previous)
                        > self.goal.progress_epsilon
                    )
            if progress is False:
                verification, reason = "FAILURE", "NO_PROGRESS"
                self.stalls += 1
            elif progress is True or near:
                self.stalls = 0
            if self.stalls >= self.limits.stall_limit:
                self.stalls = 0
                if self.recoveries >= self.limits.max_recoveries:
                    self.halt_reason = "RECOVERY_EXHAUSTED"
                else:
                    self.recoveries += 1
                    self._recovery.extend(("recover_wait", "recover_strafe", "recover_observe"))
        return ActionResult.model_validate(
            {
                **result.model_dump(),
                "verification": verification,
                "reason_code": reason,
                "evidence_observation_ids": evidence,
            }
        )

    def status(self) -> dict:
        return {
            "skill": self.last_skill,
            "stalls": self.stalls,
            "unknowns": self.unknowns,
            "recoveries": self.recoveries,
            "halt_reason": self.halt_reason,
            "arrival_evidence": list(self.arrival_evidence),
            "evidence": dict(self.last_evidence),
        }
