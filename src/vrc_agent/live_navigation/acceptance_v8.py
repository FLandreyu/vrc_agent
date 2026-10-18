"""v8 候选采信语义。没有 transport/ARM 能力；旧版本不调用此模块。"""

import math
from typing import Literal

from pydantic import Field, model_validator

from vrc_agent.contracts import ActionProposal, SceneState
from vrc_agent.skills.navigation import NavigationSkill

from .skill import CalibratedSkill
from .stability_v2 import protected_rectangles
from .stability_v5 import require_visible

SEMANTICS = "target-path-v8"
RECOGNITION = (
    "unique_label",
    "entity_confident",
    "evidence_bound",
    "bbox_legal",
    "frame_visible",
    "target_frame_bound",
    "target_not_fully_masked",
)
DECLARATION = {
    "acceptance_semantics": SEMANTICS,
    "scene_confidence_scope": "target_entity",
    "target_recognition": list(RECOGNITION),
    "target_recognition_rules": {
        "unique_label": "exactly_one_before_confidence_filter",
        "entity_min_confidence": "goal.min_confidence",
        "evidence": "singleton_current_scene_observation",
        "bbox": "finite_ordered_normalized_rectangle",
        "mask": "v6_visible_subset_not_fully_masked",
        "frame": "visible_bound_same_session_epoch",
    },
    "path_safety": {
        "predicates": [
            "scene_no_hazards",
            "scene_no_uncertainties",
            "forward_clearance",
            "recovery_clearance",
            "motion_envelope",
            "max_actions",
        ],
        "clearance": {
            "x1_max": 0.35,
            "x2_min": 0.65,
            "y1_max": 0.7,
            "y2_min": 0.9,
            "min_confidence": 0.9,
        },
        "forward_requires": [
            "scene_no_hazards",
            "scene_no_uncertainties",
            "forward_clearance",
            "motion_envelope",
            "max_actions",
        ],
        "strafe_requires": [
            "scene_no_hazards",
            "scene_no_uncertainties",
            "recovery_clearance",
            "motion_envelope",
            "max_actions",
        ],
        "yaw_requires": [
            "scene_no_hazards",
            "scene_no_uncertainties",
            "motion_envelope",
            "max_actions",
        ],
        "wait_ms": 100,
        "path_wait_limit": 3,
        "recovery_evidence": "both_lateral_steps_visible_same_geometry",
    },
    "scene_signals": {
        "target_recognition": "record_only",
        "motion": "veto_if_any",
        "arrival_success": "veto_if_any",
        "report_scope": "whole_scene_presence_no_text",
    },
    "arrival_acceptance": {
        "consecutive_new_observations": 2,
        "continuity": ["session_id", "environment_epoch", "track_id"],
        "geometry": "unchanged_selected_world",
        "hazards_or_uncertainties": "record_review_required_and_no_success",
    },
}


def scoped_confidence(entities, label):
    matches = [e for e in entities if e.label == label]
    return matches[0].confidence if len(matches) == 1 else None


class ScopedSceneV8(SceneState):
    """独立契约：未知目标置信度可为 None，仍保留其它实体供安全审计。"""

    confidence_scope: Literal["target_entity"]
    target_label: str
    source_confidence: float | None = Field(ge=0, le=1)

    @model_validator(mode="after")
    def valid_confidence(self):
        if self.confidence != scoped_confidence(self.entities, self.target_label):
            raise ValueError("v8 confidence scope mismatch")
        return self


def scope_scene(scene, label):
    return ScopedSceneV8(
        **{**scene.model_dump(), "confidence": scoped_confidence(scene.entities, label)},
        confidence_scope="target_entity",
        target_label=label,
        source_confidence=scene.confidence,
    )


class AcceptanceSkillV8(CalibratedSkill):
    def __init__(self, landmark, policy, *, profile="cautious"):
        super().__init__(landmark, profile=profile)
        self.policy = policy
        self.observations = {}
        self.decision_count = 0
        self.last_decision = None

    def register(self, observation):
        self.observations[observation.observation_id] = observation

    def candidates(self, scene):
        return [e for e in scene.entities if e.label == self.goal.label]

    def recognition_predicates(self, scene):
        matches = self.candidates(scene)
        entity = matches[0] if len(matches) == 1 else None
        observation = (
            self.observations.get(scene.observation_ids[0])
            if len(scene.observation_ids) == 1
            else None
        )
        legal = bool(
            entity
            and len(entity.bbox) == 4
            and all(math.isfinite(x) for x in entity.bbox)
            and 0 <= entity.bbox[0] < entity.bbox[2] <= 1
            and 0 <= entity.bbox[1] < entity.bbox[3] <= 1
        )
        visible = False
        if legal:
            try:
                require_visible(
                    [
                        list(r)
                        for r in protected_rectangles(
                            (entity,), {**self.policy, "protected_margin": 0}
                        )
                    ],
                    self.policy,
                )
                visible = True
            except ValueError:
                pass
        return dict(
            zip(
                RECOGNITION,
                (
                    entity is not None,
                    entity is not None and entity.confidence >= self.goal.min_confidence,
                    bool(entity and entity.evidence_observation_id in scene.observation_ids),
                    legal,
                    bool(observation and observation.window_state == "visible"),
                    bool(
                        observation
                        and observation.frame_ref is not None
                        and observation.session_id == scene.session_id
                        and observation.environment_epoch == scene.environment_epoch
                    ),
                    visible,
                ),
                strict=True,
            )
        )

    def select_target(self, scene):
        return (
            self.candidates(scene)[0] if all(self.recognition_predicates(scene).values()) else None
        )

    def clearance(self, scene, label):
        entities = [e for e in scene.entities if e.label == label]
        if len(entities) != 1:
            return False
        entity = entities[0]
        observation = self.observations.get(entity.evidence_observation_id)
        if not (
            observation
            and observation.window_state == "visible"
            and observation.frame_ref is not None
            and observation.session_id == scene.session_id
            and observation.environment_epoch == scene.environment_epoch
            and entity.evidence_observation_id in scene.observation_ids
            and entity.last_seen_ns == observation.captured_at_ns
        ):
            return False
        x1, y1, x2, y2 = entity.bbox
        return (
            entity.confidence >= 0.9
            and 0 <= x1 <= 0.35
            and 0.65 <= x2 <= 1
            and 0 <= y1 <= 0.7
            and 0.9 <= y2 <= 1
            and x1 < x2
            and y1 < y2
        )

    def arrival_permitted(self, scene):
        return not scene.hazards and not scene.uncertainties

    def recovery_allowed(self, scene):
        return self.arrival_permitted(scene) and self.clearance(scene, "recovery_clearance")

    def audit(self, scene, proposal, count):
        recognition = self.recognition_predicates(scene)
        entity = self.select_target(scene)
        path = {
            "scene_no_hazards": not scene.hazards,
            "scene_no_uncertainties": not scene.uncertainties,
            "forward_clearance": self.clearance(scene, "forward_clearance"),
            "recovery_clearance": self.clearance(scene, "recovery_clearance"),
            "motion_envelope": proposal.kind != "motion"
            or self.motion_envelope.accepts(proposal.args),
            "max_actions": 1 <= count <= self.policy["max_actions"],
        }
        if proposal.kind == "motion":
            axis = (
                "forward" if proposal.args.forward else "strafe" if proposal.args.strafe else "yaw"
            )
            required = self.policy["path_safety"][axis + "_requires"]
            permitted = all(recognition.values()) and all(path[k] for k in required)
        else:
            permitted = proposal.kind == "stop" or (
                proposal.kind == "wait" and proposal.args.duration_ms == 100 and path["max_actions"]
            )
        geometry = bool(entity and self.arrived(entity))
        return {
            "acceptance_semantics": SEMANTICS,
            "target_recognition": recognition,
            "path_safety": path,
            "proposal_permitted": permitted,
            "scope": {
                "confidence_scope": "target_entity",
                "confidence": scoped_confidence(scene.entities, self.goal.label),
                "has_hazards": bool(scene.hazards),
                "has_uncertainties": bool(scene.uncertainties),
                "target_effect": "record_only",
                "motion_effect": "veto_if_any",
                "arrival_effect": "veto_if_any",
            },
            "arrival_geometry_met": geometry,
            "arrival_review_required": geometry and not self.arrival_permitted(scene),
        }

    async def decide(self, scene, recent_results):
        self.decision_count += 1
        proposal = await NavigationSkill.decide(self, scene, recent_results)
        if proposal.kind == "motion":
            args = proposal.args
            axis = "forward" if args.forward else "strafe" if args.strafe else "yaw"
            value = getattr(self.motion_envelope, axis) * (1 if getattr(args, axis) > 0 else -1)
            proposal = ActionProposal(
                kind="motion",
                args={axis: value, "duration_ms": getattr(self.motion_envelope, axis + "_ms")},
            )
        audit = self.audit(scene, proposal, self.decision_count)
        dispatched = proposal
        if not audit["proposal_permitted"] or not self.arrival_permitted(scene):
            self.path_waits += 1
            if self.path_waits >= self.policy["path_safety"]["path_wait_limit"]:
                self.halt_reason = "PATH_UNSAFE"
            self.last_skill = "wait"
            dispatched = ActionProposal(kind="wait", args={"duration_ms": 100})
        else:
            self.path_waits = 0
        self.last_decision = {
            "observation_id": scene.observation_ids[0],
            "decision_index": self.decision_count,
            "requested": proposal.model_dump(mode="json"),
            "dispatched": dispatched.model_dump(mode="json"),
            "audit": audit,
        }
        return dispatched

    def verify(self, action, result, before, observation, after):
        verified = super().verify(action, result, before, observation, after)
        self.last_evidence["acceptance"] = self.audit(after, action, self.decision_count)
        return verified
