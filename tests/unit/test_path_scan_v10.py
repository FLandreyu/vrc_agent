"""扫描的触发、有限次数、取证门槛；纯内存数据，无采集/模型/输入。"""

import copy
import hashlib
import json
from pathlib import Path

import pytest
from test_world_v3_confidence import observed

from vrc_agent.contracts import ActionProposal, ActionRequest, ActionResult
from vrc_agent.live_navigation.path_scan import PATH_SCAN, PathScanSkill
from vrc_agent.live_navigation.stability import load_policy
from vrc_agent.live_navigation.world import load_live_world

# 冻结后摘要；候选期摘要 437849cf…bff3 为历史。
DIGEST = "a1348cc3f8b222777b8b214b56ab7bf58d4ea4f837a7dead8f0361832881cbcb"


def skill():
    return PathScanSkill(
        load_live_world("v3").landmark("G2"), load_policy("v10")[0], profile="field"
    )


def scene_for(agent, index, *, confidence=0.72, clearance=False):
    _, obs, scene = observed(confidence=confidence)
    oid, captured = f"obs-{index}", index * 1000
    obs = obs.model_copy(
        update={"observation_id": oid, "captured_at_ns": captured, "frame_ref": oid}
    )
    entity = scene.entities[0].model_copy(
        update={"evidence_observation_id": oid, "last_seen_ns": captured}
    )
    entities = (entity,)
    if clearance:
        entities += (
            entity.model_copy(
                update={
                    "label": "forward_clearance",
                    "track_id": "floor",
                    "confidence": 0.99,
                    "bbox": (0.2, 0.6, 0.9, 0.98),
                }
            ),
        )
    agent.register(obs)
    return obs, scene.model_copy(
        update={"observation_ids": (oid,), "generated_at_ns": captured, "entities": entities}
    )


def forward():
    return ActionProposal(kind="motion", args={"forward": 0.4, "duration_ms": 500})


def complete(agent, proposal, before, index, *, clearance=False, stale=False):
    action = ActionRequest(
        **proposal.model_dump(),
        session_id="memory",
        lease_id="memory-lease",
        environment_epoch=1,
        based_on_observation_id=before.observation_ids[0],
        issued_at_ns=before.generated_at_ns + 1,
        expires_at_ns=before.generated_at_ns + 1_000_000_001,
    )
    result = ActionResult(
        session_id="memory",
        action_id=action.action_id,
        status="COMPLETED",
        finished_at_ns=before.generated_at_ns + 2,
    )
    obs, after = scene_for(agent, index, clearance=clearance)
    if stale:
        obs = obs.model_copy(update={"captured_at_ns": result.finished_at_ns})
    agent.verify(action, result, before, obs, after)
    return obs, after


def test_v10_only_adds_explicit_scan_and_candidate_declarations():
    v9, old_digest = load_policy("v9")
    v10, digest = load_policy("v10")
    assert old_digest == "c9fb45ee3295766e62f3ceda2c4a744aab9dd45e580d36385168a59f97e700a2"
    assert "path_scan" not in v9 and v9["status"] == "FROZEN"
    assert v10 == {
        **v9,
        "version": "local-rgb-stability-v10-candidate",
        "status": "FROZEN",
        "path_scan": PATH_SCAN,
    }
    # 冻结只改 status 一行；摘要因此变化，旧候选摘要见 TASKS 第115/118节。
    assert digest == DIGEST
    raw = Path("src/vrc_agent/live_navigation/stability_v10.json").read_bytes()
    assert digest == hashlib.sha256(raw).hexdigest()
    path = Path("src/vrc_agent/live_navigation/stability_v10.sha256")
    assert path.read_bytes() == (digest + "\r\n").encode()
    assert raw.count(b'"status": "FROZEN"') == 1
    for profile in v10["motion_profiles"].values():
        assert PATH_SCAN["yaw_step"] == profile["yaw"] == 1.0
        assert PATH_SCAN["duration_ms"] == profile["yaw_ms"] == 200


@pytest.mark.parametrize(
    "fault", ["recognition", "clearance", "recovery", "envelope", "action_limit", "duplicate"]
)
def test_not_only_forward_clearance_failure_never_scans(fault):
    agent = skill()
    for index in range(1, 4):
        _, scene = scene_for(
            agent,
            1 if fault == "duplicate" else index,
            confidence=0.55 if fault == "recognition" else 0.72,
            clearance=fault == "clearance",
        )
        proposal = forward()
        if fault == "recovery":
            proposal = ActionProposal(kind="motion", args={"strafe": 0.2, "duration_ms": 200})
        if fault == "envelope":
            proposal = ActionProposal(
                kind="motion", args={"forward": 0.4, "yaw": 1.0, "duration_ms": 500}
            )
        agent.dispatch(scene, proposal, 41 if fault == "action_limit" else index)
        assert not agent.last_decision["audit"]["path_scan"]["triggered"]
    assert agent.scan_count == 0


def test_exact_third_denial_alternation_four_scan_limit_then_path_unsafe():
    agent = skill()
    tick = count = 0
    for sequence in range(1, 6):
        for denied in range(1, 4):
            tick += 1
            count += 1
            _, scene = scene_for(agent, tick)
            result = agent.dispatch(scene, forward(), count)
            scan = agent.last_decision["audit"]["path_scan"]
            assert scan["triggered"] is (denied == 3 and sequence <= 4)
            if scan["triggered"]:
                assert scan["sequence"] == sequence
                assert result.args.yaw == (1.0 if sequence % 2 else -1.0)
                assert result.args.forward == result.args.strafe == 0
                assert scan["yaw_audit"]["proposal_permitted"]
                assert agent.path_waits == 0
                tick += 1
                complete(agent, result, scene, tick)
                assert agent.last_evidence["path_scan"]["clearance_acquired"] is False
            else:
                assert result.kind == "wait"
    assert agent.scan_count == 4 and agent.halt_reason == "PATH_UNSAFE"


@pytest.mark.parametrize("stale", [False, True])
def test_scan_requires_post_action_new_observation_and_records_clearance(stale):
    agent = skill()
    for i in range(1, 4):
        _, before = scene_for(agent, i)
        proposal = agent.dispatch(before, forward(), i)
    with pytest.raises(ValueError, match="fresh verified"):
        agent.dispatch(before, forward(), 4)
    _, after = complete(agent, proposal, before, 4, clearance=True, stale=stale)
    evidence = agent.last_evidence["path_scan"]
    assert evidence["fresh_observation"] is (not stale)
    assert evidence["clearance_acquired"] is (not stale)
    if stale:
        assert agent.halt_reason == "PATH_UNSAFE"
    else:
        assert agent.dispatch(after, forward(), 4).args.forward == 0.4


def test_scan_yaw_cannot_bypass_envelope(monkeypatch):
    agent = skill()
    original = agent.audit

    def audit(scene, proposal, count):
        value = original(scene, proposal, count)
        if proposal.kind == "motion" and proposal.args.yaw:
            value["proposal_permitted"] = False
        return value

    monkeypatch.setattr(agent, "audit", audit)
    for i in (1, 2):
        agent.dispatch(scene_for(agent, i)[1], forward(), i)
    with pytest.raises(ValueError, match="yaw rejected"):
        agent.dispatch(scene_for(agent, 3)[1], forward(), 3)
    assert agent.scan_count == 0


def test_intervening_clearance_resets_consecutive_denial_counter():
    agent = skill()
    for i in (1, 2, 3, 4, 5, 6):
        _, scene = scene_for(agent, i, clearance=i == 3)
        agent.dispatch(scene, forward(), i)
        assert agent.last_decision["audit"]["path_scan"]["triggered"] is (i == 6)


@pytest.mark.parametrize("fault", ["missing", "bool", "limit", "yaw", "status", "threshold"])
def test_candidate_loader_rejects_mutated_declarations(monkeypatch, tmp_path, fault):
    import hashlib

    from vrc_agent.live_navigation import stability

    policy, _ = load_policy("v10")
    policy = copy.deepcopy(policy)
    if fault == "missing":
        del policy["path_scan"]["trigger_denied_attempts"]
    elif fault == "bool":
        policy["path_scan"]["enabled"] = 1
    elif fault == "status":
        # 冻结闭集{REQUIRES_FIELD_CALIBRATION, FROZEN}之外的取值必须拒绝。
        policy["status"] = "CANDIDATE"
    elif fault == "threshold":
        policy["max_changed_ppm"] = 60000
    else:
        policy["path_scan"]["max_scans_per_task" if fault == "limit" else "yaw_step"] = 5
    for p in Path("src/vrc_agent/live_navigation").glob("stability_v*.*"):
        (tmp_path / p.name).write_bytes(p.read_bytes())
    raw = json.dumps(policy).encode()
    (tmp_path / "stability_v10.json").write_bytes(raw)
    (tmp_path / "stability_v10.sha256").write_text(hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr(stability, "files", lambda _: tmp_path)
    with pytest.raises(ValueError, match="v10"):
        stability.load_policy("v10")


def test_status_closure_accepts_both_declared_values_without_relaxing_keys(monkeypatch, tmp_path):
    """闭集双向：已冻结的 v10 与候选副本都可加载，但其它键仍必须严格相等。"""
    from vrc_agent.live_navigation import stability

    policy, _ = load_policy("v10")
    assert policy["status"] == "FROZEN"
    for p in Path("src/vrc_agent/live_navigation").glob("stability_v*.*"):
        (tmp_path / p.name).write_bytes(p.read_bytes())
    monkeypatch.setattr(stability, "files", lambda _: tmp_path)

    for status in ("FROZEN", "REQUIRES_FIELD_CALIBRATION"):
        candidate = copy.deepcopy(policy)
        candidate["status"] = status
        raw = json.dumps(candidate).encode()
        (tmp_path / "stability_v10.json").write_bytes(raw)
        (tmp_path / "stability_v10.sha256").write_text(hashlib.sha256(raw).hexdigest())
        loaded, digest = stability.load_policy("v10")
        assert loaded["status"] == status and digest == hashlib.sha256(raw).hexdigest()
        # 冻结不是放宽：除版本名/状态/path_scan 外，判据必须与 v9 主体逐键相等。
        # v9 本身已 FROZEN，故 FROZEN 态下 status 不构成差异。
        v9, _ = load_policy("v9")
        differences = {k for k in set(v9) | set(loaded) if v9.get(k) != loaded.get(k)}
        expected = {"version", "path_scan"}
        if status != v9["status"]:
            expected = expected | {"status"}
        assert differences == expected

    # 闭集外取值仍拒绝，避免把“两态可加载”误读为“任意 status 可加载”。
    policy["status"] = "FROZEN_APPROVED"
    raw = json.dumps(policy).encode()
    (tmp_path / "stability_v10.json").write_bytes(raw)
    (tmp_path / "stability_v10.sha256").write_text(hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match="v10"):
        stability.load_policy("v10")
