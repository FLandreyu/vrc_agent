"""将预检、推理源与执行证据绑定到同一有界 attempt。"""


def validate_attempts(report, policy):
    if not isinstance(report.get("run_parameters"), dict):
        raise ValueError("missing attempt run parameters")
    if "max_inference_attempts" not in report.get("run_parameters", {}):
        # 历史 schema9 保留；新格式标识存在时不得缺失对应预算。
        if (
            policy["version"]
            in {
                "local-rgb-stability-v7-candidate",
                "local-rgb-stability-v8-candidate",
                "local-rgb-stability-v9-candidate",
            }
            or any("attempt" in p for p in report.get("stability_prechecks", []))
            or any(e.get("event") == "stability_attempt" for e in report["events"])
        ):
            raise ValueError("missing attempt budget")
        return
    limit = report["run_parameters"]["max_inference_attempts"]
    if type(limit) is not int or limit != policy["max_inference_attempts"]:
        raise ValueError("attempt budget mismatch")
    declared = set()
    counts = {}
    for event in report["events"]:
        if event.get("event") != "stability_attempt":
            continue
        cycle, attempt = event.get("attempt_cycle"), event.get("attempt")
        if type(cycle) is not int or cycle < 1 or type(attempt) is not int:
            raise ValueError("invalid attempt identity")
        if attempt != counts.get(cycle, 0) + 1 or not 1 <= attempt <= limit:
            raise ValueError("attempt limit exceeded or unordered")
        counts[cycle] = attempt
        declared.add((cycle, attempt))
    for item in (
        report["stability_prechecks"]
        + report["stability_proofs"]
        + [e for e in report["events"] if e.get("event") in {"inference_source", "observation"}]
    ):
        cycle, attempt = item.get("attempt_cycle"), item.get("attempt")
        if type(cycle) is not int or type(attempt) is not int or (cycle, attempt) not in declared:
            raise ValueError("undeclared attempt evidence")
    sources = {
        e["observation_id"]: e for e in report["events"] if e.get("event") == "inference_source"
    }
    for proof in report["stability_proofs"]:
        source = sources.get(proof["reference_observation_id"])
        if source is None or any(proof[k] != source[k] for k in ("attempt", "attempt_cycle")):
            raise ValueError("cross-attempt action evidence")
    for source in sources.values():
        if not any(
            p["stable"]
            and p["reference_observation_id"] == source["observation_id"]
            and all(p[k] == source[k] for k in ("attempt", "attempt_cycle"))
            for p in report["stability_prechecks"]
        ):
            raise ValueError("attempt missing accepted precheck")
