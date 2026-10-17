"""真实时间/IPC 导航循环；输入只经独立控制进程，绝不 ARM/RESET。"""

import asyncio
import json
from datetime import datetime, timezone

from vrc_agent.clock import SystemClock
from vrc_agent.contracts import ActionProposal, ActionResult, SceneState
from vrc_agent.control.service import ControllerProcess, bind_request
from vrc_agent.providers.base import CancelToken, Deadline
from vrc_agent.providers.errors import ProviderError
from vrc_agent.runtime import RuntimeHalt

from .parameters import action_freshness_ms, motion_profile
from .skill import CalibratedSkill


def wire_result(payload):
    # IPC 的 tuple 已编码为 JSON 数组；使用 JSON 契约解析，不能放宽 strict 校验。
    return ActionResult.model_validate_json(json.dumps(payload))


class SupervisedNavigation:
    """生产装配只传 live ControllerProcess；测试可用其 dry-run 子进程验证同一 IPC。"""

    def __init__(
        self,
        client: ControllerProcess,
        source_factory,
        perception,
        world,
        task,
        *,
        arm_timeout_s=60.0,
        task_timeout_s=30.0,
        inference_ms=1000,
        clock=None,
    ):
        if not isinstance(client, ControllerProcess):
            raise ValueError("independent ControllerProcess required")
        if not 0 < arm_timeout_s <= 120 or not 0 < task_timeout_s <= 180:
            raise ValueError("invalid supervised time budget")
        if not 1 <= inference_ms <= 1000:
            raise ValueError("inference must fit fresh-observation budget")
        self.client, self.source_factory, self.perception = client, source_factory, perception
        self.world, self.task = world, task
        self.clock = clock or SystemClock()
        if client.config.navigation_motion_profile != "cautious" and getattr(
            self, "POLICY_VERSION", None
        ) not in {"v6", "v7"}:
            raise ValueError("field motion envelope requires v6 runtime")
        self.skill = CalibratedSkill(
            world.landmark(task.group), profile=client.config.navigation_motion_profile
        )
        self.motion_envelope = motion_profile(client.config.navigation_motion_profile)
        self.max_actions = None
        self.action_freshness_ms = action_freshness_ms(client.config)
        self.arm_timeout_s, self.task_timeout_s, self.inference_ms = (
            arm_timeout_s,
            task_timeout_s,
            inference_ms,
        )
        self.cancel = CancelToken()
        self.source = None
        self.authority = None
        self.ends_at = None
        self.seen = set()
        self.last_capture = -1
        self.actions, self.results, self.events = [], [], []
        self.pending = None
        self.started = False

    async def rpc(self, operation, payload=None):
        return await asyncio.to_thread(self.client.request, operation, payload, 0.3)

    def event(self, name, **fields):
        self.events.append({"event": name, "at_ns": self.clock.now_ns(), **fields})

    async def check_authority(self):
        snapshot = await self.rpc("status")
        if self.cancel.cancelled or snapshot["state"] not in {"ARMED_IDLE", "EXECUTING"}:
            raise RuntimeHalt(
                snapshot["reason_code"] if snapshot["reason_code"] != "OK" else "NOT_ARMED"
            )
        if self.authority is not None and any(
            snapshot[k] != self.authority[k]
            for k in ("session_id", "environment_epoch", "lease_id")
        ):
            raise RuntimeHalt("EPOCH_MISMATCH")
        if self.clock.now_ns() >= snapshot["lease_expires_ns"]:
            raise RuntimeHalt("EXPIRED_LEASE")
        if self.ends_at is not None and (self.clock.now_ns() / 1_000_000_000) >= self.ends_at:
            raise RuntimeHalt("TASK_TIMEOUT")
        return snapshot

    async def wait_for_arm(self):
        end = (self.clock.now_ns() / 1_000_000_000) + self.arm_timeout_s
        while (self.clock.now_ns() / 1_000_000_000) < end:
            snapshot = await self.rpc("status")
            if snapshot["state"] == "ARMED_IDLE":
                self.authority = snapshot
                self.ends_at = (self.clock.now_ns() / 1_000_000_000) + self.task_timeout_s
                return await self.check_authority()
            if snapshot["state"] != "DISARMED":
                raise RuntimeHalt(
                    snapshot["reason_code"] if snapshot["reason_code"] != "OK" else "NOT_ARMED"
                )
            await asyncio.sleep(0.05)
        raise RuntimeHalt("ARM_TIMEOUT")

    async def bounded(self, awaitable, *, ms, reason):
        task = asyncio.ensure_future(awaitable)
        end = (self.clock.now_ns() / 1_000_000_000) + ms / 1000
        try:
            while True:
                await self.check_authority()
                if (self.clock.now_ns() / 1_000_000_000) >= end:
                    raise RuntimeHalt(reason)
                if task.done():
                    return task.result()
                await asyncio.wait(
                    {task}, timeout=min(0.03, max(0, end - (self.clock.now_ns() / 1_000_000_000)))
                )
        finally:
            if not task.done():
                task.cancel()
                task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            elif not task.cancelled():
                task.exception()

    async def capture(self, after_ns=None):
        max_age = self.action_freshness_ms * 1_000_000
        if after_ns is None:
            return await self._capture_once(after_ns, max_age)

        # 动作结束与采集发布存在竞态。只丢弃未严格更新的候选，不重写时间/ID；
        # 整个等待共用一个截止期，不能逐帧重置1500ms窗口或跳过授权检查。
        retry_ms = min(1500, self.action_freshness_ms)
        retry_until_ns = self.clock.now_ns() + retry_ms * 1_000_000

        async def wait_for_new_frame():
            for attempt in range(50):
                captured = await self._capture_once(after_ns, max_age, retry_until_ns)
                if captured is not None:
                    return captured
                if attempt < 49:
                    await asyncio.sleep(0.03)
            raise RuntimeHalt("STALE_OBSERVATION")

        return await self.bounded(wait_for_new_frame(), ms=retry_ms, reason="STALE_OBSERVATION")

    async def _capture_once(self, after_ns, max_age, retry_until_ns=None):
        observation = await self.bounded(
            asyncio.to_thread(self.source.next_observation), ms=1000, reason="CAPTURE_TIMEOUT"
        )
        snapshot = await self.check_authority()
        if (
            observation.session_id != snapshot["session_id"]
            or observation.environment_epoch != snapshot["environment_epoch"]
        ):
            raise RuntimeHalt("EPOCH_MISMATCH")
        if observation.window_state != "visible" or observation.frame_ref is None:
            raise RuntimeHalt("FOCUS_LOST")
        if observation.frame_size != self.world.frame_size:
            raise RuntimeHalt("CALIBRATION_MISMATCH")
        now = self.clock.now_ns()
        if not 0 <= now - observation.captured_at_ns < max_age or (
            retry_until_ns is not None and now >= retry_until_ns
        ):
            raise RuntimeHalt("STALE_OBSERVATION")
        if (
            observation.observation_id in self.seen
            or observation.captured_at_ns <= self.last_capture
            or (after_ns is not None and observation.captured_at_ns <= after_ns)
        ):
            if retry_until_ns is not None:
                return None
            raise RuntimeHalt("STALE_OBSERVATION")
        frame = self.source.frame_for(observation)
        if (
            frame is None
            or frame.ref != observation.frame_ref
            or frame.captured_at_ns != observation.captured_at_ns
            or frame.size != observation.frame_size
        ):
            raise RuntimeHalt("MISSING_FRAME")
        self.seen.add(observation.observation_id)
        self.last_capture = observation.captured_at_ns
        if not await self.rpc("observe", observation.model_dump(mode="json")):
            raise RuntimeHalt("STALE_OBSERVATION")
        return observation, frame, max_age

    async def before_submit(self, observation, scene):
        return observation, scene

    async def observe(self, after_ns=None):
        observation, frame, max_age = await self.capture(after_ns)
        deadline = Deadline.from_now(self.clock, self.inference_ms)
        scene = await self.bounded(
            self.perception.interpret(
                observation, frame=frame, deadline=deadline, cancel=self.cancel
            ),
            ms=self.inference_ms,
            reason="MODEL_LATE",
        )
        await self.check_authority()
        if not 0 <= self.clock.now_ns() - observation.captured_at_ns < max_age:
            raise RuntimeHalt("STALE_OBSERVATION")
        scene = SceneState.model_validate(scene.model_dump())
        if (
            scene.session_id != observation.session_id
            or scene.environment_epoch != observation.environment_epoch
            or scene.observation_ids != (observation.observation_id,)
            or not observation.captured_at_ns <= scene.generated_at_ns <= self.clock.now_ns()
            or any(
                e.evidence_observation_id != observation.observation_id
                or e.last_seen_ns != observation.captured_at_ns
                for e in scene.entities
            )
        ):
            raise RuntimeHalt("INVALID_SCENE_EVIDENCE")
        self.event(
            "observation",
            observation_id=observation.observation_id,
            captured_at_ns=observation.captured_at_ns,
            frame_digest=frame.digest(),
            confidence=scene.confidence,
            has_hazards=bool(scene.hazards),
            has_uncertainties=bool(scene.uncertainties),
            entities=[
                e.model_dump(mode="json")
                for e in scene.entities
                if e.label in {self.skill.goal.label, "forward_clearance", "recovery_clearance"}
            ],
        )
        return observation, scene

    async def run(self):
        if self.started:
            raise ValueError("supervised sessions cannot be reused")
        self.started = True
        outcome, final_state = "ACTION_LIMIT", "UNKNOWN"
        started_at = datetime.now(timezone.utc).isoformat()
        try:
            authority = await self.wait_for_arm()
            self.source = self.source_factory(authority)
            observation, scene = await self.observe()
            for _ in range(
                self.max_actions if self.max_actions is not None else self.skill.limits.max_actions
            ):
                observation, scene = await self.before_submit(observation, scene)
                raw = await self.bounded(
                    self.skill.decide(scene, tuple(self.results[-8:])), ms=200, reason="MODEL_LATE"
                )
                proposal = ActionProposal.model_validate(
                    raw.model_dump() if hasattr(raw, "model_dump") else raw
                )
                if self.skill.halt_reason:
                    outcome = self.skill.halt_reason
                    break
                if proposal.kind not in {"motion", "wait", "stop"}:
                    raise RuntimeHalt("UNSUPPORTED_CAPABILITY")
                # 再次校验，任何新供应商或替换策略不能绕过本运行时的运动范围。
                from vrc_agent.planner import target

                if proposal.kind == "motion":
                    args = proposal.args
                    if target(scene, self.skill.goal) is None:
                        raise RuntimeHalt("UNSAFE_SCENE")
                    if not self.motion_envelope.accepts(args):
                        raise RuntimeHalt("INVALID_ARGS")
                    if args.strafe and not self.skill.recovery_allowed(scene):
                        raise RuntimeHalt("RECOVERY_UNSAFE")
                    if args.forward and not self.skill.clearance(scene, "forward_clearance"):
                        raise RuntimeHalt("PATH_UNSAFE")
                if proposal.kind == "wait" and proposal.args.duration_ms != 100:
                    raise RuntimeHalt("INVALID_ARGS")
                authority = await self.check_authority()
                request = bind_request(proposal, observation, authority, clock=self.clock)
                self.pending = request
                self.actions.append(request)
                result = wire_result(await self.rpc("submit", request.model_dump(mode="json")))
                end = (self.clock.now_ns() / 1_000_000_000) + 1
                while result.status == "RUNNING":
                    if (self.clock.now_ns() / 1_000_000_000) >= end:
                        raise RuntimeHalt("CONTROL_TIMEOUT")
                    await asyncio.sleep(0.02)
                    result = wire_result(await self.rpc("result", {"action_id": request.action_id}))
                self.results.append(result.model_copy(update={"verification": "UNKNOWN"}))
                self.pending = None
                if result.status != "COMPLETED":
                    outcome = result.reason_code
                    break
                await self.check_authority()
                if proposal.kind == "stop":
                    outcome = "MANUAL_STOP"
                    break
                after, new_scene = await self.observe(after_ns=result.finished_at_ns)
                verified = self.skill.verify(request, result, scene, after, new_scene)
                self.results[-1] = verified
                self.event(
                    "navigation_feedback", action_id=request.action_id, **self.skill.status()
                )
                if self.skill.halt_reason:
                    outcome = self.skill.halt_reason
                    break
                if verified.verification == "SUCCESS":
                    # 成功不得覆盖验证时发生的接管/租约失效。
                    await self.check_authority()
                    outcome = "SUCCESS"
                    break
                observation, scene = after, new_scene
        except RuntimeHalt as exc:
            outcome = str(exc)
        except ProviderError as exc:
            # 预算耗尽是预期运行状态：给出明确结局码，循环结束后的统一 pause 会让控制器停在
            # PAUSED（不会带着耗尽的预算继续发下一次付费调用）。
            outcome = (
                "BUDGET_EXCEEDED"
                if exc.failure == "BUDGET_EXCEEDED"
                else "MODEL_" + str(exc.failure)
            )
        except asyncio.CancelledError:
            outcome = "MANUAL_STOP"
        except (OSError, EOFError, TimeoutError):
            outcome = "CONTROL_UNAVAILABLE"
        except Exception as exc:
            outcome = "RUNTIME_ERROR"
            self.event("runtime_error", error_type=type(exc).__name__)
        finally:
            self.cancel.cancel()
            try:
                snapshot = await self.rpc("status")
                if snapshot["state"] == "ESTOP":
                    outcome = "MANUAL_STOP"
                elif outcome == "SUCCESS" and any(
                    snapshot[k] != self.authority[k]
                    for k in ("session_id", "environment_epoch", "lease_id")
                ):
                    outcome = "EPOCH_MISMATCH"
                elif outcome == "SUCCESS" and snapshot["state"] != "ARMED_IDLE":
                    outcome = (
                        snapshot["reason_code"] if snapshot["reason_code"] != "OK" else "NOT_ARMED"
                    )
                await self.rpc("estop" if outcome == "MANUAL_STOP" else "pause")
                final_snapshot = await self.rpc("status")
                final_state = final_snapshot["state"]
                if final_state == "ESTOP":
                    outcome = "MANUAL_STOP"
                elif final_snapshot["reason_code"] == "TRANSPORT_ERROR":
                    outcome = "TRANSPORT_ERROR"
                if self.pending:
                    final = await self.rpc("result", {"action_id": self.pending.action_id})
                    if final:
                        self.results.append(
                            wire_result(final).model_copy(update={"verification": "UNKNOWN"})
                        )
            except (OSError, EOFError, TimeoutError, ValueError):
                outcome = "CONTROL_UNAVAILABLE"
                # IPC 不可达时停止心跳，子进程 watchdog 独立归零。
                self.client.stop_heartbeat.set()
            if self.source is not None:
                try:
                    self.source.close()
                except Exception as exc:
                    self.event("source_close_failed", error_type=type(exc).__name__)
                    outcome = "SOURCE_CLOSE_FAILED"
        return {
            "schema_version": 1,
            "mode": "live" if self.client.config.mode == "live" else "supervised-dry-run",
            "sends_live_input": self.client.config.mode == "live",
            "world_version": self.world.version,
            "suite_digest": self.world.digest(),
            "task_id": self.task.task_id,
            "session_id": self.client.session_id,
            "controller_pid": self.client.process.pid,
            "started_at": started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "outcome": outcome,
            "success": outcome == "SUCCESS",
            "final_state": final_state,
            "recoveries": self.skill.recoveries,
            "actions": [
                a.model_dump(mode="json", exclude={"event_id", "lease_id", "expected_outcome"})
                for a in self.actions
            ],
            "results": [r.model_dump(mode="json", exclude={"event_id"}) for r in self.results],
            "events": self.events,
            "field_acceptance": "REQUIRES_HUMAN_REVIEW",
        }
