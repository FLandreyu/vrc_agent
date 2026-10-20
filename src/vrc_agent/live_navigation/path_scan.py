"""v10有界换视角技能；复用v8识别/路径审计，不授予任何控制权限。"""

from vrc_agent.contracts import ActionProposal
from vrc_agent.skills.navigation import NavigationSkill

from .acceptance_v8 import AcceptanceSkillV8

PATH_SCAN = {
    "enabled": True,
    "trigger_denied_attempts": 3,
    "max_scans_per_task": 4,
    "yaw_step": 1.0,
    "duration_ms": 200,
    "alternate_direction": True,
    "initial_direction": 1,
    "reset_path_waits_per_scan": True,
    "post_scan_requires_new_observation": True,
    "trigger": "recognized_forward_denied_only_by_forward_clearance",
    "exhaustion": "unchanged_path_wait_limit_PATH_UNSAFE",
    "post_scan_evidence": "new_inference_source_after_finished_with_both_checkpoints",
}


class PathScanSkill(AcceptanceSkillV8):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.scan_count = 0
        self.denied_streak = 0
        self.pending_scan = None
        self.scan_observations = set()

    async def decide(self, scene, recent_results):
        proposal = await NavigationSkill.decide(self, scene, recent_results)
        if proposal.kind == "motion":
            args = proposal.args
            axis = "forward" if args.forward else "strafe" if args.strafe else "yaw"
            value = getattr(self.motion_envelope, axis) * (1 if getattr(args, axis) > 0 else -1)
            proposal = ActionProposal(
                kind="motion",
                args={axis: value, "duration_ms": getattr(self.motion_envelope, axis + "_ms")},
            )
        return self.dispatch(scene, proposal, self.decision_count + 1)

    def dispatch(self, scene, proposal, count):
        """纯观测/请求驱动状态迁移；报告重放必须调用同一入口，不能相信计数器。"""
        if self.pending_scan is not None:
            raise ValueError("scan requires a fresh verified observation before another decision")
        self.decision_count = count
        audit = self.audit(scene, proposal, count)
        path = audit["path_safety"]
        eligible = bool(
            proposal.kind == "motion"
            and proposal.args.forward > 0
            and proposal.args.strafe == proposal.args.yaw == 0
            and all(audit["target_recognition"].values())
            and path["motion_envelope"]
            and path["max_actions"]
            and not path["forward_clearance"]
        )
        observation_id = scene.observation_ids[0]
        fresh = observation_id not in self.scan_observations
        self.scan_observations.add(observation_id)
        self.denied_streak = self.denied_streak + 1 if eligible and fresh else 0
        rule = self.policy["path_scan"]  # 必填声明，不对旧版或缺键提供默认。
        triggered = bool(
            rule["enabled"]
            and eligible
            and fresh
            and self.denied_streak >= rule["trigger_denied_attempts"]
            and self.scan_count < rule["max_scans_per_task"]
        )
        scan_audit = {
            "eligible": eligible,
            "new_observation": fresh,
            "denied_streak": self.denied_streak,
            "triggered": triggered,
            "trigger_reason": "forward_clearance_only" if eligible else None,
            "sequence": None,
            "direction": None,
            "scans_used": self.scan_count,
            "exhausted": self.scan_count >= rule["max_scans_per_task"],
            "yaw_audit": None,
        }
        dispatched = proposal
        if triggered:
            direction = rule["initial_direction"] * (-1 if self.scan_count % 2 else 1)
            dispatched = ActionProposal(
                kind="motion",
                args={"yaw": direction * rule["yaw_step"], "duration_ms": rule["duration_ms"]},
            )
            yaw_audit = self.audit(scene, dispatched, count)
            if not yaw_audit["proposal_permitted"]:
                raise ValueError("scan yaw rejected by unchanged motion audit")
            self.scan_count += 1
            scan_audit.update(
                sequence=self.scan_count,
                direction=direction,
                scans_used=self.scan_count,
                exhausted=self.scan_count >= rule["max_scans_per_task"],
                yaw_audit=yaw_audit,
            )
            self.pending_scan = {
                "sequence": self.scan_count,
                "direction": direction,
                "before_observation_id": observation_id,
            }
            self.path_waits = self.denied_streak = 0
            self.last_skill = "scan"  # 换视角不是朝向目标纠偏，不伪称前进/居中进展。
        elif not audit["proposal_permitted"]:
            self.path_waits += 1
            if self.path_waits >= self.policy["path_safety"]["path_wait_limit"]:
                self.halt_reason = "PATH_UNSAFE"
            self.last_skill = "wait"
            dispatched = ActionProposal(kind="wait", args={"duration_ms": 100})
        else:
            self.path_waits = 0
        audit["path_scan"] = scan_audit
        self.last_decision = {
            "observation_id": observation_id,
            "decision_index": count,
            "requested": proposal.model_dump(mode="json"),
            "dispatched": dispatched.model_dump(mode="json"),
            "audit": audit,
        }
        return dispatched

    def verify(self, action, result, before, observation, after):
        verified = super().verify(action, result, before, observation, after)
        if self.pending_scan is not None:
            predicates = self.last_evidence["verification_predicates"]
            fresh = all(
                predicates[k]
                for k in (
                    "result_completed",
                    "result_ok",
                    "result_action_matches",
                    "result_session_matches",
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
                )
            )
            self.last_evidence["path_scan"] = {
                **self.pending_scan,
                "action_id": action.action_id,
                "after_observation_id": observation.observation_id,
                "fresh_observation": fresh,
                "clearance_acquired": fresh and self.clearance(after, "forward_clearance"),
            }
            if fresh:
                self.pending_scan = None
            else:
                self.halt_reason = "PATH_UNSAFE"
        return verified
