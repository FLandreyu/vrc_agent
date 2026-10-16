"""离线汇总复验方向 A 的证据链；不是对报告或原始像素的数字签名。"""

import re

from vrc_agent.contracts import Entity

from .stability import load_policy
from .stability_v2 import protected_rectangles

SCHEMAS = {"v1": 2, "v2": 3, "v3": 4, "v4": 5, "v5": 6}
WARMUP_VERSIONS = {"v2", "v3", "v4", "v5"}


def policy_verdict(metrics, policy):
    """按策略 metric 分派复验实现；未知 metric 不得静默通过。"""
    metric = policy["metric"]
    if metric == "rgb_tolerance_local_guard_v2":
        from .stability_v2 import verdict
    elif metric == "rgb_tolerance_area_guard_v3":
        from .stability_v3 import verdict
    elif metric == "rgb_tolerance_visible_guard_v5":
        from .stability_v5 import verdict
    else:
        raise ValueError("unknown stability metric")
    return verdict(metrics, policy)


def validate_proof(proof, policy, digest, session_id, version):
    if proof.get("session_id") != session_id or type(proof.get("environment_epoch")) is not int:
        raise ValueError("stability authority mismatch")
    if proof.get("policy_digest") != digest:
        raise ValueError("stability proof policy mismatch")
    for key in ("reference_digest", "current_digest", "reference_rgb_digest", "current_rgb_digest"):
        if not isinstance(proof.get(key), str) or not re.fullmatch("[0-9a-f]{64}", proof[key]):
            raise ValueError("invalid stability digest")
    times = [
        proof.get(k)
        for k in ("reference_captured_at_ns", "current_captured_at_ns", "checked_at_ns")
    ]
    if any(type(t) is not int or t < 0 for t in times):
        raise ValueError("invalid stability timestamp")
    if not times[0] < times[1] <= times[2] < times[1] + policy["freshness_ms"] * 1_000_000:
        raise ValueError("stale stability proof")
    key = "equal" if version == "v1" else "stable"
    if type(proof.get("black")) is not bool or type(proof.get(key)) is not bool:
        raise ValueError("invalid stability verdict")
    expected = (
        (not proof["black"] and proof["reference_rgb_digest"] == proof["current_rgb_digest"])
        if version == "v1"
        else policy_verdict(proof, policy)
    )
    if proof[key] != expected:
        raise ValueError("contradictory stability verdict")
    return expected


def validate_warmup(report, sources, policy, digest, version="v2"):
    checks, references = report.get("warmup_checks"), report.get("warmup_references")
    if not isinstance(checks, list) or not isinstance(references, dict):
        raise ValueError("missing warmup evidence")
    ids = set()
    for check in checks:
        validate_proof(check, policy, digest, report["session_id"], version)
        if check.get("protected_rectangles") != [] or "validates_observation_id" in check:
            raise ValueError("invalid warmup evidence")
        if check["current_observation_id"] in ids:
            raise ValueError("repeated warmup observation")
        ids.add(check["current_observation_id"])
        if (
            check["current_captured_at_ns"] - check["reference_captured_at_ns"]
            < policy["warmup_interval_ms"] * 1_000_000
        ):
            raise ValueError("warmup interval too short")
    if not set(sources) <= set(references):
        raise ValueError("source missing warmup")
    for reference_id, indices in references.items():
        if (
            not isinstance(indices, list)
            or len(indices) != policy["warmup_pairs"]
            or any(type(i) is not int or not 0 <= i < len(checks) for i in indices)
        ):
            raise ValueError("invalid warmup indices")
        if indices != list(range(indices[0], indices[0] + len(indices))):
            raise ValueError("nonconsecutive warmup")
        chain = [checks[i] for i in indices]
        if not all(check["stable"] for check in chain):
            raise ValueError("unstable warmup source")
        for previous, current in zip(chain, chain[1:]):
            for left, right in (
                ("current_observation_id", "reference_observation_id"),
                ("current_captured_at_ns", "reference_captured_at_ns"),
                ("current_digest", "reference_digest"),
            ):
                if previous[left] != current[right]:
                    raise ValueError("broken warmup chain")
            if previous["environment_epoch"] != current["environment_epoch"]:
                raise ValueError("warmup epoch mismatch")
        last = chain[-1]
        if reference_id != last["current_observation_id"]:
            raise ValueError("warmup reference mismatch")
        if reference_id in sources:
            source = sources[reference_id]
            if (
                source["captured_at_ns"] != last["current_captured_at_ns"]
                or source["frame_digest"] != last["current_digest"]
                or source["environment_epoch"] != last["environment_epoch"]
            ):
                raise ValueError("source warmup mismatch")


def validate_stability_report(report):
    declared = report.get("navigation_design")
    if declared is None and report.get("schema_version", 1) == 1:
        if "stability_policy" in report or "stability_proofs" in report:
            raise ValueError("undeclared stability evidence")
        return
    version = {
        "stable-inference-v1": "v1",
        "stable-inference-v2": "v2",
        "stable-inference-v3": "v3",
        "stable-inference-v4": "v4",
        "stable-inference-v5": "v5",
    }.get(declared)
    if version is None or report.get("schema_version") != SCHEMAS[version]:
        raise ValueError("unknown navigation design")
    if version == "v5" and type(report.get("schema_version")) is not int:
        raise ValueError("invalid visible schema type")
    policy, digest = load_policy(version)
    if report.get("stability_policy") != {"digest": digest, **policy}:
        raise ValueError("stability policy mismatch")
    proofs = report.get("stability_proofs")
    if not isinstance(proofs, list):
        raise ValueError("missing stability proofs")
    sources, observations = {}, {}
    for event in report["events"]:
        kind = event.get("event")
        if kind in {"inference_source", "observation"}:
            mapping = sources if kind == "inference_source" else observations
            key = event["observation_id"]
            if key in mapping:
                raise ValueError("duplicate stability observation")
            mapping[key] = event
    if version in WARMUP_VERSIONS:
        validate_warmup(report, sources, policy, digest, version)
    if version == "v5":
        from .stability_v5 import audit_entities, verify_audit

        for source in sources.values():
            entities = [
                Entity.model_validate({**e, "bbox": tuple(e["bbox"])}) for e in source["entities"]
            ]
            verify_audit(source, audit_entities(entities, policy))
    accepted, bindings = {}, {}
    for proof in proofs:
        is_stable = validate_proof(proof, policy, digest, report["session_id"], version)
        times = [proof["reference_captured_at_ns"], proof["current_captured_at_ns"]]
        source = sources.get(proof["reference_observation_id"])
        if (
            source is None
            or source["captured_at_ns"] != times[0]
            or source["frame_digest"] != proof["reference_digest"]
        ):
            raise ValueError("missing inference source")
        if (
            source.get("session_id") != proof["session_id"]
            or source.get("environment_epoch") != proof["environment_epoch"]
        ):
            raise ValueError("source authority mismatch")
        for entity in source["entities"]:
            # JSON 的 bbox 为 list；转换到契约要求的 tuple，其余严格校验。
            entity = Entity.model_validate({**entity, "bbox": tuple(entity["bbox"])})
            if (
                entity.evidence_observation_id != proof["reference_observation_id"]
                or entity.last_seen_ns != times[0]
            ):
                raise ValueError("source entity mismatch")
        if version in WARMUP_VERSIONS:
            entities = [
                Entity.model_validate({**e, "bbox": tuple(e["bbox"])}) for e in source["entities"]
            ]
            if proof.get("protected_rectangles") != [
                list(r) for r in protected_rectangles(entities, policy)
            ]:
                raise ValueError("protected evidence regions mismatch")
            if version == "v5":
                verify_audit(proof, audit_entities(entities, policy))
        if is_stable:
            accepted[proof["current_observation_id"]] = proof
            if "validates_observation_id" in proof:
                bindings[proof["validates_observation_id"]] = proof
        elif "validates_observation_id" in proof:
            raise ValueError("rejected proof used for action")
    for key, observation in observations.items():
        proof = accepted.get(key)
        if proof is None or observation.get("derived_from") != proof["reference_observation_id"]:
            raise ValueError("unproven derived observation")
        source = sources[proof["reference_observation_id"]]
        if (
            observation["captured_at_ns"] != proof["current_captured_at_ns"]
            or observation["frame_digest"] != proof["current_digest"]
        ):
            raise ValueError("derived frame mismatch")
        for field in ("confidence", "has_hazards", "has_uncertainties"):
            if observation[field] != source[field]:
                raise ValueError("derived scene mismatch")
        expected_entities = [
            {**e, "evidence_observation_id": key, "last_seen_ns": observation["captured_at_ns"]}
            for e in source["entities"]
        ]
        if observation["entities"] != expected_entities:
            raise ValueError("derived entities mismatch")
    for action in report["actions"]:
        key = action["based_on_observation_id"]
        if key not in bindings or key not in observations:
            raise ValueError("action missing stability checkpoint")
        proof, observation = bindings[key], observations[key]
        if (
            action["session_id"] != proof["session_id"]
            or action["environment_epoch"] != proof["environment_epoch"]
        ):
            raise ValueError("action authority mismatch")
        if proof["reference_observation_id"] != observation["derived_from"]:
            raise ValueError("action checkpoint source mismatch")
        issued = action["issued_at_ns"]
        if (
            type(issued) is not int
            or not proof["checked_at_ns"]
            <= issued
            < observation["captured_at_ns"] + policy["freshness_ms"] * 1_000_000
        ):
            raise ValueError("action stability expired")
