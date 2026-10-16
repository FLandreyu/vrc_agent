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
    def __init__(self, *args, policy_version="v1", **kwargs):
        # 父类仍保留 1000ms 构造约束，旧装配不改变；新预算仅取冻结策略。
        super().__init__(*args, **kwargs)
        self.policy_version = policy_version
        self.policy, self.policy_digest = load_policy(policy_version)
        if self.client.config.mode == "live" and self.policy["status"] != "FROZEN":
            raise ValueError("candidate stability policy cannot enable live navigation")
        self.inference_ms = self.policy["inference_ms"]
        self.task_timeout_s = self.policy["task_timeout_s"]
        self.reference = None
        self.proofs = []

    def accepted(self, proof):
        return proof["equal" if self.policy_version == "v1" else "stable"]

    def evidence_entities(self, scene):
        return tuple(
            e
            for e in scene.entities
            if e.label in {self.skill.goal.label, "forward_clearance", "recovery_clearance"}
        )

    async def reference_capture(self, after_ns):
        return await self.capture(after_ns)

    async def fresh_proof(self, reference_obs, reference_frame, *, after_ns=None, entities=()):
        current, frame, max_age = await self.capture(after_ns)
        proof = await self.bounded(
            asyncio.to_thread(compare, reference_frame, frame, self.policy, entities),
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
            observation, frame, _ = await self.reference_capture(after_ns)
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
            current, current_frame, proof = await self.fresh_proof(
                observation, frame, entities=self.evidence_entities(source_scene)
            )
            if self.accepted(proof):
                self.reference = (observation, frame, source_scene)
                return self.derive(source_scene, current, current_frame, proof)
            self.event("stability_rejected")
        raise RuntimeHalt("SCENE_UNSTABLE")

    async def before_submit(self, observation, scene):
        for _ in range(self.policy["max_inference_attempts"]):
            source, frame, original = self.reference
            _, _, proof = await self.fresh_proof(
                source, frame, entities=self.evidence_entities(original)
            )
            max_age = min(1500, self.client.config.control.observation_max_age_ms) * 1_000_000
            if (
                self.accepted(proof)
                and 0 <= self.clock.now_ns() - observation.captured_at_ns < max_age
            ):
                # 不刷新绑定观测的采集时间；额外帧仅证明这份仍新鲜的证据可以继续使用。
                proof["validates_observation_id"] = observation.observation_id
                return observation, scene
            self.event("stability_rejected")
            observation, scene = await self.observe()
        raise RuntimeHalt("SCENE_UNSTABLE")

    async def run(self):
        report = await super().run()
        report["navigation_design"] = "stable-inference-" + self.policy_version
        report["schema_version"] = {"v1": 2, "v2": 3, "v3": 4}[self.policy_version]
        report["stability_policy"] = {"digest": self.policy_digest, **self.policy}
        report["stability_proofs"] = self.proofs
        return report


class StableNavigationV2(StableNavigation):
    """v2 每次推理前都丢弃预热帧；不跨动作复用参考，不链式滚动推理参考。"""

    POLICY_VERSION = "v2"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, policy_version=self.POLICY_VERSION, **kwargs)
        self.warmup_checks = []
        self.warmup_references = {}

    async def reference_capture(self, after_ns):
        previous, previous_frame, _ = await self.capture(after_ns)
        consecutive = []
        for _ in range(self.policy["warmup_max_frames"] - 1):
            await self.bounded(
                asyncio.sleep(self.policy["warmup_interval_ms"] / 1000),
                ms=1000,
                reason="STABILITY_TIMEOUT",
            )
            current, frame, max_age = await self.capture(after_ns)
            proof = await self.bounded(
                asyncio.to_thread(compare, previous_frame, frame, self.policy),
                ms=1000,
                reason="STABILITY_TIMEOUT",
            )
            now = self.clock.now_ns()
            if not 0 <= now - current.captured_at_ns < max_age:
                raise RuntimeHalt("STALE_OBSERVATION")
            proof.update(
                reference_observation_id=previous.observation_id,
                current_observation_id=current.observation_id,
                reference_captured_at_ns=previous.captured_at_ns,
                current_captured_at_ns=current.captured_at_ns,
                checked_at_ns=now,
                policy_digest=self.policy_digest,
                session_id=current.session_id,
                environment_epoch=current.environment_epoch,
            )
            self.warmup_checks.append(proof)
            if self.accepted(proof):
                consecutive.append(len(self.warmup_checks) - 1)
            else:
                consecutive.clear()
            if len(consecutive) == self.policy["warmup_pairs"]:
                self.warmup_references[current.observation_id] = list(consecutive)
                return current, frame, max_age
            previous, previous_frame = current, frame
        raise RuntimeHalt("WARMUP_UNSTABLE")

    async def run(self):
        report = await super().run()
        report["warmup_checks"] = self.warmup_checks
        report["warmup_references"] = self.warmup_references
        return report


class StableNavigationV3(StableNavigationV2):
    """v3 保留预热与保护区，改用全局面积上限；不再使用逐块局部面积判据。

    现场证据（`docs/validation/T08_stability_v2_field.md` 第 6 节）显示最安静的
    窗口里单个 32×32 块的连续变化也远超 v2 阈值，而环境动画还会形成同量级的
    连续变化区，因此 v3 去掉 `max_tile_changed` 这一维，只保留全局面积上限与
    证据保护区零容差。预热、检查点、报告格式与 v2 相同，仅换策略判据。
    """

    POLICY_VERSION = "v3"
