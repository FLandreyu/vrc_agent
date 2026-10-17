"""慢模型的停止—推理—本机复验路径；不修改控制进程或旧感知时效策略。"""

import asyncio
from dataclasses import asdict

from vrc_agent.contracts import SceneState, new_id
from vrc_agent.perception.scene import build_scene
from vrc_agent.providers.base import Deadline, ModelInput
from vrc_agent.providers.images import encode_bgra
from vrc_agent.runtime import RuntimeHalt

from .parameters import action_freshness_ms
from .runtime import SupervisedNavigation
from .skill import calibrated_prompt
from .stability import (
    compare,
    intersects_mask,
    load_policy,
    masked_protection_overlap,
    rejects_masked_protection,
)
from .stability_v2 import protected_rectangles


class StableNavigation(SupervisedNavigation):
    POLICY_VERSION = "v1"

    def __init__(self, *args, policy_version=None, **kwargs):
        # 版本只能由实现类决定：直接给基类传 v2/v3/v4 会在任何采集/模型/动作前拒绝，
        # 防止调用方选到新判据却省掉对应预热（复核记录 F2）。
        if policy_version is not None and policy_version != self.POLICY_VERSION:
            raise ValueError("stability policy version must match the navigation class")
        # 父类仍保留 1000ms 构造约束，旧装配不改变；新预算仅取冻结策略。
        super().__init__(*args, **kwargs)
        policy_version = self.POLICY_VERSION
        self.policy_version = policy_version
        self.policy, self.policy_digest = load_policy(policy_version)
        if self.client.config.mode == "live" and self.policy["status"] != "FROZEN":
            raise ValueError("candidate stability policy cannot enable live navigation")
        self.inference_ms = self.policy["inference_ms"]
        self.task_timeout_s = self.policy["task_timeout_s"]
        self.action_freshness_ms = action_freshness_ms(self.client.config, self.policy)
        if policy_version == "v6":
            self.max_actions = self.policy["max_actions"]
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
        if self.masked_scene(source_scene):
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

    def masked_scene(self, scene):
        return any(intersects_mask(e, self.policy) for e in scene.entities)

    def source_audit(self, entities):
        return {}

    def reject_masked_protection(self, entities):
        """v4 声明：外扩保护矩形与遮罩相交时，在比较/派生/执行前中止。

        相交处被遮罩清零，比较器无法证明保护区零变化；此时必须拒绝，不能靠
        事后报告审计（复核记录 F1）。
        """
        if rejects_masked_protection(self.policy) and masked_protection_overlap(
            protected_rectangles(entities, self.policy), self.policy
        ):
            raise RuntimeHalt("MASKED_SCENE_EVIDENCE")

    async def observe(self, after_ns=None):
        for _ in range(self.policy["max_inference_attempts"]):
            observation, frame, _ = await self.reference_capture(after_ns)
            # 编码在 worker 内，主循环继续每 30ms 检查授权；provider 不再同步编码大帧。
            image, media_type = await self.bounded(
                asyncio.to_thread(
                    encode_bgra,
                    frame.data,
                    frame.size,
                    image_format=self.client.config.perception.image_format,
                    jpeg_quality=self.client.config.perception.jpeg_quality,
                ),
                ms=1000,
                reason="IMAGE_ENCODING_TIMEOUT",
            )
            request = ModelInput(
                prompt=calibrated_prompt(self.world.landmark(self.task.group)),
                observation_id=observation.observation_id,
                request_id=new_id(),
                image=image,
                image_media_type=media_type,
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
            evidence = self.evidence_entities(source_scene)
            self.reject_masked_protection(evidence)
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
                **self.source_audit(evidence),
            )
            current, current_frame, proof = await self.fresh_proof(
                observation, frame, entities=evidence
            )
            if self.accepted(proof):
                self.reference = (observation, frame, source_scene)
                return self.derive(source_scene, current, current_frame, proof)
            self.event("stability_rejected")
        raise RuntimeHalt("SCENE_UNSTABLE")

    async def before_submit(self, observation, scene):
        for _ in range(self.policy["max_inference_attempts"]):
            source, frame, original = self.reference
            evidence = self.evidence_entities(original)
            self.reject_masked_protection(evidence)
            _, _, proof = await self.fresh_proof(source, frame, entities=evidence)
            max_age = self.action_freshness_ms * 1_000_000
            if (
                self.accepted(proof)
                and 0 <= self.clock.now_ns() - observation.captured_at_ns < max_age
            ):
                # 不刷新绑定观测的采集时间；额外帧仅证明这份仍新鲜的证据可以继续使用。
                proof["validates_observation_id"] = observation.observation_id
                return observation, scene
            self.event("stability_rejected")
            observation, scene = await self.retry_observe()
        raise RuntimeHalt("SCENE_UNSTABLE")

    async def retry_observe(self):
        return await self.observe()

    async def run(self):
        report = await super().run()
        report["navigation_design"] = "stable-inference-" + self.policy_version
        report["schema_version"] = {"v1": 2, "v2": 3, "v3": 4, "v4": 5, "v5": 6, "v6": 7}[
            self.policy_version
        ]
        report["stability_policy"] = {"digest": self.policy_digest, **self.policy}
        report["stability_proofs"] = self.proofs
        if self.policy_version == "v6":
            report["run_parameters"] = {
                "motion_profile": self.client.config.navigation_motion_profile,
                "motion_envelope": asdict(self.motion_envelope),
                "freshness_ms": self.action_freshness_ms,
                "task_timeout_s": self.task_timeout_s,
                "max_actions": self.max_actions,
            }
        return report


class StableNavigationV2(StableNavigation):
    """v2 每次推理前都丢弃预热帧；不跨动作复用参考，不链式滚动推理参考。"""

    POLICY_VERSION = "v2"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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


class StableNavigationV4(StableNavigationV3):
    """v4 与 v3 判据相同，只多一条已声明的保护语义：保护矩形不得与遮罩相交。

    v3 独立复核发现外扩保护区与遮罩交叠时，交集里的变化被遮罩清零、仍可宣称
    「保护区零变化」（F1）。按「判据/保护语义变更走新版本与摘要」的流程，不改
    v3 本身，而是发布 v4：策略显式声明 `protected_mask_policy=reject_on_overlap`，
    运行时在比较/派生前中止 `MASKED_SCENE_EVIDENCE`，报告复验同样拒绝。
    v1/v2/v3 未声明该字段，保持既有已复核行为不变。
    """

    POLICY_VERSION = "v4"


class StableNavigationV5(StableNavigationV4):
    """v5 只证明未遮罩的导航证据区域，部分交叠为声明放行。"""

    POLICY_VERSION = "v5"

    def source_audit(self, entities):
        from .stability_v5 import audit_entities

        return audit_entities(entities, self.policy)

    def reject_masked_protection(self, entities):
        try:
            self.source_audit(entities)
        except ValueError as exc:
            raise RuntimeHalt("MASKED_SCENE_EVIDENCE") from exc

    def masked_scene(self, scene):
        # derive 对全部实体检查原 bbox；不仅检查写入导航证据的三个标签。
        from .stability_v5 import require_visible

        try:
            raw = [
                list(r)
                for r in protected_rectangles(
                    scene.entities, {**self.policy, "protected_margin": 0}
                )
            ]
            require_visible(raw, self.policy)
        except ValueError:
            return True
        return False


class StableNavigationV6(StableNavigationV5):
    """v5判据不变；预检只增加拒绝，不能代替两个执行证据检查点。"""

    POLICY_VERSION = "v6"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.prechecks = []
        self.retry_reference = None

    async def timed(self, stage, awaitable):
        started = self.clock.now_ns()
        try:
            return await awaitable
        finally:
            self.event("stage_timing", stage=stage, duration_ns=self.clock.now_ns() - started)

    async def capture(self, after_ns=None):
        return await self.timed("capture", super().capture(after_ns))

    async def bounded(self, awaitable, *, ms, reason):
        stage = {
            "IMAGE_ENCODING_TIMEOUT": "encode",
            "INFERENCE_TIMEOUT": "infer",
            "STABILITY_TIMEOUT": "stability",
        }.get(reason)
        operation = super().bounded(awaitable, ms=ms, reason=reason)
        return await self.timed(stage, operation) if stage else await operation

    async def rpc(self, operation, payload=None):
        request = super().rpc(operation, payload)
        return await self.timed("submit", request) if operation == "submit" else await request

    async def observe(self, after_ns=None):
        # 每次 observe（包括动作后）重建预热；只在本次重试内复用，不跨动作滚动参考。
        self.retry_reference = None
        return await super().observe(after_ns)

    async def retry_observe(self):
        # before_submit 拒绝后也不重启预热；两个原检查点均只增加拒绝。
        raise RuntimeHalt("SCENE_UNSTABLE")

    async def reference_capture(self, after_ns):
        if self.retry_reference is not None:
            # 模型后的复验已经拒绝，不重发同一推理源或重新预热来扩大预算。
            raise RuntimeHalt("SCENE_UNSTABLE")
        self.retry_reference = await super().reference_capture(after_ns)
        observation, frame, max_age = self.retry_reference
        evidence = self.evidence_entities(self.reference[2]) if self.reference else ()
        # 不拿预检帧作推理源，不新增派生链；原参考仍由两对连续预热直接证明。
        # 一旦其超过新鲜度预算即拒绝，不靠重启整轮预热无限消耗任务时间。
        for _ in range(self.policy["max_inference_attempts"]):
            if not 0 <= self.clock.now_ns() - observation.captured_at_ns < max_age:
                raise RuntimeHalt("SCENE_UNSTABLE")
            _, _, proof = await self.fresh_proof(
                observation, frame, after_ns=after_ns, entities=evidence
            )
            self.proofs.pop()  # 预检不是 after_inference / before_submit 执行证据。
            self.prechecks.append(proof)
            self.event("stability_precheck", accepted=self.accepted(proof))
            if self.accepted(proof):
                return observation, frame, max_age
        raise RuntimeHalt("SCENE_UNSTABLE")

    async def run(self):
        from .timings import summarize_timings

        report = await super().run()
        report["schema_version"] = 8
        report["stability_prechecks"] = self.prechecks
        report["stage_timings"] = summarize_timings(self.events)
        return report


STABLE_NAVIGATION_CLASSES = {
    "v1": StableNavigation,
    "v2": StableNavigationV2,
    "v3": StableNavigationV3,
    "v4": StableNavigationV4,
    "v5": StableNavigationV5,
    "v6": StableNavigationV6,
}


def stable_navigation_class(policy_version):
    """按策略版本装配运行时；版本选择集中在此，避免调用方选到判据却漏掉预热。"""
    try:
        return STABLE_NAVIGATION_CLASSES[policy_version]
    except KeyError:
        raise ValueError("unknown stability policy version") from None
