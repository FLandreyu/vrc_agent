"""运行时阶段计时；分位数采用 nearest-rank，可从事件逐项重算。"""

import math

STAGES = ("capture", "encode", "infer", "stability", "submit")


def summarize_timings(events):
    values = {stage: [] for stage in STAGES}
    for event in events:
        if event.get("event") != "stage_timing":
            continue
        stage, duration = event.get("stage"), event.get("duration_ns")
        if stage not in values or type(duration) is not int or not 0 <= duration <= 2**53:
            raise ValueError("invalid stage timing")
        if type(event.get("at_ns")) is not int or event["at_ns"] < duration:
            raise ValueError("invalid timing timestamp")
        values[stage].append(duration)
    result = {}
    for stage, samples in values.items():
        samples.sort()
        result[stage] = {"count": len(samples)}
        for name, percentile in (("p50_ms", 0.5), ("p95_ms", 0.95)):
            result[stage][name] = (
                samples[math.ceil(len(samples) * percentile) - 1] / 1_000_000 if samples else None
            )
    return result


def validate_timings(report):
    expected = summarize_timings(report["events"])
    actual = report.get("stage_timings")
    if actual != expected:
        raise ValueError("stage timing summary mismatch")
    for stage in STAGES:
        if type(actual[stage]["count"]) is not int:
            raise ValueError("invalid timing count")
        for key in ("p50_ms", "p95_ms"):
            value = actual[stage][key]
            if value is not None and type(value) is not float:
                raise ValueError("invalid timing quantile")
