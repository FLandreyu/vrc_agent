"""离线汇总复验方向 A 的证据链；不是对报告或原始像素的数字签名。"""

import re

from vrc_agent.contracts import Entity

from .stability import load_policy


def validate_stability_report(report):
    declared = report.get("navigation_design")
    if declared is None and report.get("schema_version", 1) == 1:
        if "stability_policy" in report or "stability_proofs" in report:
            raise ValueError("undeclared stability evidence")
        return
    if declared != "stable-inference-v1" or report.get("schema_version") != 2:
        raise ValueError("unknown navigation design")
    policy, digest = load_policy()
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
    accepted, bindings = {}, {}
    for proof in proofs:
        if (
            proof.get("session_id") != report.get("session_id")
            or type(proof.get("environment_epoch")) is not int
        ):
            raise ValueError("stability authority mismatch")
        if proof.get("policy_digest") != digest:
            raise ValueError("stability proof policy mismatch")
        for key in (
            "reference_digest",
            "current_digest",
            "reference_rgb_digest",
            "current_rgb_digest",
        ):
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
        if type(proof.get("black")) is not bool or type(proof.get("equal")) is not bool:
            raise ValueError("invalid stability verdict")
        expected = (
            not proof["black"] and proof["reference_rgb_digest"] == proof["current_rgb_digest"]
        )
        if proof["equal"] != expected:
            raise ValueError("contradictory stability verdict")
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
        if proof["equal"]:
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
