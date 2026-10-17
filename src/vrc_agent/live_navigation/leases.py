"""控制进程租约账本重算；结构一致性验证，不是热键签名。"""

import math

RECORD_KEYS = {"at_ns", "granted_ms", "expires_ns", "sequence"}
BUDGET_KEYS = {
    "base_ms",
    "extensions",
    "total_ms",
    "extension_ms",
    "extension_max",
    "task_timeout_ms",
    "armed_at_ns",
    "control_until_ns",
    "elapsed_ms",
}


def integer(value, minimum=0, maximum=2**53):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid lease integer")
    return value


def validate_leases(report, policy):
    budget, records = report.get("control_budget"), report.get("lease_extensions")
    if not isinstance(budget, dict) or set(budget) != BUDGET_KEYS or not isinstance(records, list):
        raise ValueError("missing control budget or lease records")
    base = integer(budget["base_ms"], 500, 60000)
    step = integer(budget["extension_ms"], 1, 30000)
    limit = integer(budget["extension_max"], 0, 5)
    task_ms = integer(budget["task_timeout_ms"], 1, 180000)
    if task_ms != policy["task_timeout_s"] * 1000 or base + step * limit > task_ms:
        raise ValueError("lease configuration exceeds task timeout")
    if integer(budget["extensions"], 0, limit) != len(records):
        raise ValueError("lease extension count mismatch")
    lease_events = [e for e in report["events"] if e.get("event") == "lease_extended"]
    if any(not isinstance(r, dict) or set(r) != RECORD_KEYS for r in records):
        raise ValueError("invalid lease record")
    expected_events = [
        {"event": "lease_extended", "reason_code": "LEASE_EXTENDED", **r} for r in records
    ]
    if lease_events != expected_events:
        raise ValueError("missing or inconsistent lease event")
    started, until = budget["armed_at_ns"], budget["control_until_ns"]
    if started is None:
        if records or report["actions"] or until is not None or budget["elapsed_ms"] is not None:
            raise ValueError("unarmed control budget has activity")
        if integer(budget["total_ms"], base, task_ms) != base:
            raise ValueError("unarmed control total mismatch")
        return
    integer(started)
    integer(until, started)
    expires, total, last_at = started + base * 1_000_000, base, started
    for sequence, record in enumerate(records, 1):
        at = integer(record["at_ns"], last_at, until)
        if at >= expires or integer(record["sequence"], 1, limit) != sequence:
            raise ValueError("expired or unordered lease extension")
        granted = min(step, task_ms - total)
        if granted <= 0 or integer(record["granted_ms"], 1, step) != granted:
            raise ValueError("invalid lease grant")
        expires += granted * 1_000_000
        if integer(record["expires_ns"]) != expires:
            raise ValueError("lease expiry mismatch")
        total += granted
        last_at = at
    if integer(budget["total_ms"], base, task_ms) != total or until > expires:
        raise ValueError("elapsed control exceeds granted lease")
    elapsed = budget["elapsed_ms"]
    if (
        type(elapsed) is not float
        or not math.isfinite(elapsed)
        or elapsed != (until - started) / 1_000_000
    ):
        raise ValueError("lease elapsed mismatch")
    for action in report["actions"]:
        issued = integer(action.get("issued_at_ns"), started, until)
        valid_until = started + base * 1_000_000
        for record in records:
            if record["at_ns"] <= issued:
                valid_until = record["expires_ns"]
        if issued >= valid_until:
            raise ValueError("action issued outside granted lease")
        integer(action.get("expires_at_ns"), issued + 1, issued + 1_000_000_000)
