"""慢模型的停止—推理—本机复验路径；不修改控制进程或旧感知时效策略。"""

import asyncio

from vrc_agent.contracts import SceneState, new_id
from vrc_agent.perception.scene import build_scene
from vrc_agent.providers.base import Deadline, ModelInput
from vrc_agent.providers.images import bgra_png
from vrc_agent.runtime import RuntimeHalt

from .runtime import SupervisedNavigation
from .skill import calibrated_prompt
from .stability import compare, intersects_mask, load_policy


class StableNavigation(SupervisedNavigation):
    def __init__(self, *args, **kwargs):
        # 父类仍保留 1000ms 构造约束，旧装配不改变；新预算仅取冻结策略。
        super().__init__(*args, **kwargs)
        self.policy, self.policy_digest = load_policy()
        if self.client.config.mode == "live" and self.policy["status"] != "FROZEN":
            raise ValueError("candidate stability policy cannot enable live navigation")
        self.inference_ms = self.policy["inference_ms"]
        self.task_timeout_s = self.policy["task_timeout_s"]
        self.reference = None
        self.proofs = []

    async def fresh_proof(self, reference_obs, reference_frame, *, after_ns=None):
        current, frame, max_age = await self.capture(after_ns)
        proof = await self.bounded(
            asyncio.to_thread(compare, reference_frame, frame, self.policy),
            ms=1000,
            reason="STABILITY_TIMEOUT",
        )
        now = self.clock.now_ns()
        if not 0 <= now - current.captured_at_ns < max_age:
            raise RuntimeHalt("STALE_OBSERVATION")
        proof.update(
            session_id=current.session_id,
            environment_epoch=current.environment_epoch,
            reference_observation_id=reference_obs.observation_id,
            current_observation_id=current.observation_id,
            reference_captured_at_ns=reference_obs.captured_at_ns,
            current_captured_at_ns=current.captured_at_ns,
            checked_at_ns=now,
            policy_digest=self.policy_digest,
        )
        self.proofs.append(proof)
        return current, frame, proof

    def derive(self, source_scene, current, frame, proof):
        # 原始 SceneState 保留在 reference；派生关系独立记录，绝不重写旧 Observation。
        if any(intersects_mask(e, self.policy) for e in source_scene.entities):
            raise RuntimeHalt("MASKED_SCENE_EVIDENCE")
        scene = SceneState(
            session_id=current.session_id,
            environment_epoch=current.environment_epoch,
            observation_ids=(current.observation_id,),
            generated_at_ns=self.clock.now_ns(),
            entities=tuple(
                e.model_copy(
                    update={
                        "evidence_observation_id": current.observation_id,
                        "last_seen_ns": current.captured_at_ns,
                    }
                )
                for e in source_scene.entities
            ),
            confidence=source_scene.confidence,
            hazards=source_scene.hazards,
            uncertainties=source_scene.uncertainties,
        )
        self.event(
            "observation",
            observation_id=current.observation_id,
            captured_at_ns=current.captured_at_ns,
            frame_digest=proof["current_digest"],
            confidence=scene.confidence,
            has_hazards=bool(scene.hazards),
            has_uncertainties=bool(scene.uncertainties),
            entities=[
                e.model_dump(mode="json")
                for e in scene.entities
                if e.label in {self.skill.goal.label, "forward_clearance", "recovery_clearance"}
            ],
            derived_from=proof["reference_observation_id"],
        )
        return current, scene

    async def observe(self, after_ns=None):
        for _ in range(self.policy["max_inference_attempts"]):
            observation, frame, _ = await self.capture(after_ns)
            # 编码在 worker 内，主循环继续每 30ms 检查授权；provider 不再同步编码大帧。
            png = await self.bounded(
                asyncio.to_thread(bgra_png, frame.data, frame.size),
                ms=1000,
                reason="IMAGE_ENCODING_TIMEOUT",
            )
            request = ModelInput(
                prompt=calibrated_prompt(self.world.landmark(self.task.group)),
                observation_id=observation.observation_id,
                request_id=new_id(),
                image=png,
                image_media_type="image/png",
                image_size=frame.size,
                image_ref=frame.ref,
                session_id=observation.session_id,
                environment_epoch=observation.environment_epoch,
                created_at_ns=observation.captured_at_ns,
            )
            response = await self.bounded(
                self.perception.provider.infer(
                    request,
                    deadline=Deadline.from_now(self.clock, self.inference_ms),
                    cancel=self.cancel,
                ),
                ms=self.inference_ms,
                reason="INFERENCE_TIMEOUT",
            )
            if (
                response.request_id != request.request_id
                or response.observation_id != observation.observation_id
            ):
                raise RuntimeHalt("INVALID_SCENE_EVIDENCE")
            source_scene = build_scene(
                response.payload,
                observation,
                generated_at_ns=self.clock.now_ns(),
                max_entities=64,
            ).scene
            self.event(
                "inference_source",
                observation_id=observation.observation_id,
                captured_at_ns=observation.captured_at_ns,
                frame_digest=frame.digest(),
                session_id=observation.session_id,
                environment_epoch=observation.environment_epoch,
                confidence=source_scene.confidence,
                has_hazards=bool(source_scene.hazards),
                has_uncertainties=bool(source_scene.uncertainties),
                entities=[
                    e.model_dump(mode="json")
                    for e in source_scene.entities
                    if e.label in {self.skill.goal.label, "forward_clearance", "recovery_clearance"}
                ],
            )
            current, current_frame, proof = await self.fresh_proof(observation, frame)
            if proof["equal"]:
                self.reference = (observation, frame, source_scene)
                return self.derive(source_scene, current, current_frame, proof)
            self.event("stability_rejected")
        raise RuntimeHalt("SCENE_UNSTABLE")

    async def before_submit(self, observation, scene):
        for _ in range(self.policy["max_inference_attempts"]):
            source, frame, _ = self.reference
            _, _, proof = await self.fresh_proof(source, frame)
            max_age = min(1500, self.client.config.control.observation_max_age_ms) * 1_000_000
            if proof["equal"] and 0 <= self.clock.now_ns() - observation.captured_at_ns < max_age:
                # ???????????????????????????????
                proof["validates_observation_id"] = observation.observation_id
                return observation, scene
            self.event("stability_rejected")
            observation, scene = await self.observe()
        raise RuntimeHalt("SCENE_UNSTABLE")

    async def run(self):
        report = await super().run()
        report["navigation_design"] = "stable-inference-v1"
        report["schema_version"] = 2
        report["stability_policy"] = {"digest": self.policy_digest, **self.policy}
        report["stability_proofs"] = self.proofs
        return report
