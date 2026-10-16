"""T06 离线主循环：感知、短动作、权限绑定、执行和基于新观测的验证。

只接离线 ReplayController；真实客户端仍使用原独立控制进程，不在这里自动启用。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable

from vrc_agent.capture.errors import ReplayExhausted
from vrc_agent.contracts import ActionProposal, ActionRequest, ActionResult, Observation, SceneState
from vrc_agent.control.replay import ReplayController
from vrc_agent.control.service import bind_request
from vrc_agent.perception.interpreter import Perception
from vrc_agent.planner import NavigationGoal, Planner, ResultVerifier, area, target
from vrc_agent.providers.base import CancelToken, Deadline
from vrc_agent.providers.errors import ProviderError
from vrc_agent.skills.navigation import NavigationSkill


class RuntimeHalt(Exception):
    """可信运行时的终止原因，不包含模型文本或异常中的私密载荷。"""


class Runtime:
    def __init__(
        self,
        source,
        perception: Perception,
        planner: Planner,
        controller: ReplayController,
        goal: NavigationGoal,
        *,
        max_actions: int = 8,
        no_progress_limit: int = 2,
        model_timeout_ms: int = 500,
        navigation: NavigationSkill | None = None,
    ):
        if not isinstance(controller, ReplayController):
            raise ValueError("T06 Runtime only supports the offline replay controller")
        if not 1 <= max_actions <= 100 or not 1 <= no_progress_limit <= 10:
            raise ValueError("invalid runtime action limits")
        if not 1 <= model_timeout_ms <= 1000:
            raise ValueError("invalid inference deadline")
        self.source, self.perception, self.planner = source, perception, planner
        self.controller, self.goal = controller, goal
        self.clock = controller.clock
        if navigation is not None and (planner is not navigation or navigation.goal != goal):
            raise ValueError("navigation must be the trusted planner for this goal")
        self.navigation = navigation
        self.verifier = navigation or ResultVerifier(goal)
        self.max_actions, self.no_progress_limit = max_actions, no_progress_limit
        self.model_timeout_ms = model_timeout_ms
        self.cancel = CancelToken()
        self.results: list[ActionResult] = []
        self.events: list[dict] = []
        self.actions: list[ActionRequest] = []
        self._pending: ActionRequest | None = None
        self._seen: set[str] = set()
        self._last_capture = -1
        self._started = False

    def emergency_stop(self):
        # 不等待模型协作取消；先停止控制层，再通知推理。
        self.controller.stop(emergency=True)
        self.cancel.cancel()

    def _event(self, event: str, **fields):
        self.events.append(
            {
                "schema_version": 1,
                "event_id": f"runtime-{len(self.events) + 1:04d}",
                "session_id": self.controller.session_id,
                "at_ns": self.clock.now_ns(),
                "event": event,
                **fields,
            }
        )

    def _authority(self):
        snapshot = self.controller.snapshot()
        if self.cancel.cancelled or snapshot["state"] not in {"ARMED_IDLE", "EXECUTING"}:
            raise RuntimeHalt(
                (snapshot["reason_code"] if snapshot["reason_code"] != "OK" else "NOT_ARMED")
                if not self.cancel.cancelled
                else "MANUAL_STOP"
            )
        return snapshot

    def _check_observation(self, observation: Observation):
        snapshot = self._authority()
        if observation.session_id != snapshot["session_id"]:
            raise RuntimeHalt("SESSION_MISMATCH")
        if observation.environment_epoch != snapshot["environment_epoch"]:
            raise RuntimeHalt("EPOCH_MISMATCH")
        age = self.clock.now_ns() - observation.captured_at_ns
        if not 0 <= age < 1_500_000_000:
            raise RuntimeHalt("STALE_OBSERVATION")
        if observation.window_state != "visible" or observation.frame_ref is None:
            raise RuntimeHalt("FOCUS_LOST")

    async def _bounded(self, call: Awaitable, deadline: Deadline):
        # 双重截止期：虚拟时钟覆盖回放延迟；墙钟覆盖真的 await 悬挂。
        snapshot = self.controller.snapshot()
        task = asyncio.ensure_future(call)
        try:
            done, _ = await asyncio.wait({task}, timeout=self.model_timeout_ms / 1000)
            self._authority()
            current = self.controller.snapshot()
            if (current["environment_epoch"], current["lease_id"]) != (
                snapshot["environment_epoch"],
                snapshot["lease_id"],
            ):
                raise RuntimeHalt("EPOCH_MISMATCH")
            if not done or deadline.expired(self.clock.now_ns()):
                raise RuntimeHalt("MODEL_LATE")
            return task.result()
        finally:
            if not task.done():
                task.cancel()
                # 不 await 一个无视取消的供应商；迟到结果永远不会进入动作链。
                task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
            elif not task.cancelled():
                task.exception()  # 即使权限失效先于取结果，也消费已完成的异常。

    async def _observe_scene(self, *, after_ns: int | None = None):
        observation = self.source.next_observation()
        if after_ns is not None and observation.captured_at_ns <= after_ns:
            raise RuntimeHalt("STALE_OBSERVATION")
        if (
            observation.observation_id in self._seen
            or observation.captured_at_ns <= self._last_capture
        ):
            raise RuntimeHalt("STALE_OBSERVATION")
        self._seen.add(observation.observation_id)
        self._last_capture = observation.captured_at_ns
        self._check_observation(observation)
        if not self.controller.observe(observation):
            raise RuntimeHalt("STALE_OBSERVATION")
        frame = self.source.frame_for(observation)
        if (
            frame is None
            or frame.ref != observation.frame_ref
            or frame.captured_at_ns != observation.captured_at_ns
            or frame.size != observation.frame_size
        ):
            raise RuntimeHalt("MISSING_FRAME")
        self._event(
            "observation",
            observation_id=observation.observation_id,
            session_id=observation.session_id,
            environment_epoch=observation.environment_epoch,
        )
        deadline = Deadline.from_now(self.clock, self.model_timeout_ms)
        scene = await self._bounded(
            self.perception.interpret(
                observation,
                frame=frame,
                deadline=deadline,
                cancel=self.cancel,
            ),
            deadline,
        )
        self._check_observation(observation)
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
        self._event(
            "scene",
            observation_id=observation.observation_id,
            confidence=scene.confidence,
            uncertain=bool(scene.uncertainties),
        )
        return observation, scene

    async def run(self) -> dict:
        if self._started:
            raise ValueError("runtime sessions cannot be reused")
        self._started = True
        outcome = "ACTION_LIMIT"
        stalls = 0
        try:
            observation, scene = await self._observe_scene()
            for index in range(self.max_actions):
                self._check_observation(observation)
                visible_target = target(scene, self.goal)
                if (
                    self.navigation is None
                    and visible_target
                    and area(visible_target) >= self.goal.arrival_area
                ):
                    outcome = "SUCCESS"
                    break
                deadline = Deadline.from_now(self.clock, self.model_timeout_ms)
                raw = await self._bounded(
                    self.planner.decide(scene, tuple(self.results[-8:])), deadline
                )
                self._check_observation(observation)
                # 即使已有 Pydantic 实例也复验；拒绝 Request/模型自带权限及多余字段。
                proposal = ActionProposal.model_validate(
                    raw.model_dump() if hasattr(raw, "model_dump") else raw
                )
                if proposal.kind not in {"motion", "wait", "stop"}:
                    raise RuntimeHalt("UNSUPPORTED_CAPABILITY")
                if proposal.kind == "motion" and target(scene, self.goal) is None:
                    raise RuntimeHalt("UNSAFE_SCENE")
                request = bind_request(proposal, observation, self._authority(), clock=self.clock)
                # 回放 ID 由可信运行时编号；不采纳模型提供的 action_id。
                request = ActionRequest.model_validate(
                    {**request.model_dump(), "action_id": f"action-{index + 1:04d}"}
                )
                self.actions.append(request)
                self._pending = request
                self._event(
                    "proposal_bound",
                    action_id=request.action_id,
                    observation_id=observation.observation_id,
                    kind=request.kind,
                )
                result = self.controller.submit(request)
                while result.status == "RUNNING":
                    await asyncio.sleep(0)
                    self.controller.advance_to(self.clock.now_ns() + 10_000_000)
                    result = self.controller.result(request.action_id)
                result = ActionResult.model_validate(
                    {**result.model_dump(), "verification": "UNKNOWN"}
                )
                self.results.append(result)
                self._pending = None
                if result.status != "COMPLETED":
                    outcome = result.reason_code
                    break
                if request.kind == "stop":
                    outcome = "MANUAL_STOP"
                    break
                new_observation, new_scene = await self._observe_scene(
                    after_ns=result.finished_at_ns
                )
                result = self.verifier.verify(request, result, scene, new_observation, new_scene)
                self.results[-1] = result
                self._event(
                    "verification",
                    action_id=result.action_id,
                    observation_id=new_observation.observation_id,
                    verification=result.verification,
                    reason_code=result.reason_code,
                )
                if self.navigation is not None:
                    self._event(
                        "navigation_feedback",
                        action_id=result.action_id,
                        **self.navigation.status(),
                    )
                    if self.navigation.halt_reason:
                        self.controller.stop("NO_PROGRESS")
                        outcome = self.navigation.halt_reason
                        break
                if result.verification == "SUCCESS":
                    outcome = "SUCCESS"
                    break
                stalls = stalls + 1 if result.reason_code == "NO_PROGRESS" else 0
                if self.navigation is None and stalls >= self.no_progress_limit:
                    self.controller.stop("NO_PROGRESS")
                    outcome = "NO_PROGRESS"
                    break
                observation, scene = new_observation, new_scene
        except ReplayExhausted:
            outcome = "REPLAY_EXHAUSTED"
        except ProviderError as exc:
            # 预算耗尽是**预期运行状态**（ARCHITECTURE 状态机：预算耗尽 → PAUSED），
            # 如实给出原因码并暂停控制器；其它供应商失败仍按运行时错误处理。
            if exc.failure == "BUDGET_EXCEEDED":
                outcome = "BUDGET_EXCEEDED"
                self.controller.stop("BUDGET_EXCEEDED")
            else:
                outcome = "RUNTIME_ERROR"
                self._event("runtime_error", error_type=type(exc).__name__)
        except RuntimeHalt as exc:
            outcome = str(exc)
        except asyncio.CancelledError:
            self.emergency_stop()
            raise
        except Exception as exc:
            # 供应商/采集编程错误也必须归零，不记录可能携带密钥的异常文本。
            outcome = "RUNTIME_ERROR"
            self._event("runtime_error", error_type=type(exc).__name__)
        finally:
            self.cancel.cancel()
            self.controller.close()
            if self._pending is not None:
                try:
                    final = self.controller.result(self._pending.action_id)
                except KeyError:
                    final = ActionResult(
                        session_id=self._pending.session_id,
                        action_id=self._pending.action_id,
                        status="FAILED",
                        reason_code="TRANSPORT_ERROR",
                    )
                self.results.append(
                    ActionResult.model_validate({**final.model_dump(), "verification": "UNKNOWN"})
                )
                self._pending = None
            self.source.close()
        self._event("runtime_finished", outcome=outcome)
        report = {
            "mode": "offline-simulation",
            "sends_live_input": False,
            "session_id": self.controller.snapshot()["session_id"],
            "outcome": outcome,
            "actions": [
                a.model_dump(mode="json", exclude={"event_id", "lease_id"}) for a in self.actions
            ],
            "results": [r.model_dump(mode="json", exclude={"event_id"}) for r in self.results],
            "events": self.events,
            "control_events": self.controller.events,
            "messages": self.controller.messages,
            "final_state": self.controller.snapshot()["state"],
        }
        report["sequence_digest"] = hashlib.sha256(
            json.dumps(report, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return report
