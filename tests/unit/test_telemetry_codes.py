"""T10：错误码登记表是**闭集**，未知码必须原样保留而不是并进最近的家族。

这里逐条锁住三件事：

1. 各层自己的失败闭集都被收进登记表（改一层忘登记会在这里失败）；
2. 未知码 `family_of` 返回 `None`、`tally` 单独列出，绝不冒充已知码；
3. `codes_of` 只按字段名取码——绝不从任意字符串里猜「这看起来像个错误码」。
"""

from __future__ import annotations

from vrc_agent.capture.errors import CAPTURE_FAILURES
from vrc_agent.contracts import REASONS
from vrc_agent.dialogue.errors import DIALOGUE_FAILURES
from vrc_agent.perception.errors import PERCEPTION_FAILURES
from vrc_agent.providers.errors import PROVIDER_FAILURES
from vrc_agent.telemetry import codes as tcode


def test_registry_covers_every_layer_closed_set() -> None:
    known = set(tcode.KNOWN_CODES)

    for source in (
        REASONS,
        PROVIDER_FAILURES,
        PERCEPTION_FAILURES,
        CAPTURE_FAILURES,
        DIALOGUE_FAILURES,
        tcode.NAVIGATION_FAILURE_CLASSES,
        tcode.RUNTIME_CODES,
        tcode.VERIFICATION_CODES,
        tcode.TELEMETRY_CODES,
    ):
        assert set(source) <= known, sorted(set(source) - known)


def test_registry_is_stable_and_families_round_trip() -> None:
    assert tcode.KNOWN_CODES == tuple(tcode.CODE_REGISTRY)
    grouped = tcode.families()
    assert grouped["control"] == REASONS[: len(grouped["control"])]
    for code in tcode.KNOWN_CODES:
        assert tcode.is_known(code)
        assert tcode.family_of(code) is not None
        assert code in tcode.registry()[code].code


def test_unknown_code_gets_no_family() -> None:
    assert tcode.is_known("NOT_A_REAL_CODE") is False
    assert tcode.family_of("NOT_A_REAL_CODE") is None


def test_misspelled_code_is_kept_separate_from_its_family() -> None:
    report = tcode.tally(["MANUAL_STOP", "MANUAL_STOP", "MANUAL_STOPS"])

    assert report["by_family"] == {"control": 2}
    assert report["unknown_codes"] == {"MANUAL_STOPS": 1}
    assert report["total"] == 3


def test_codes_of_reads_only_named_fields() -> None:
    document = {
        "outcome": "STUCK",
        "nested": [{"reason_code": "MODEL_TIMEOUT"}, {"note": "this is not a code"}],
        "results": ({"failure_class": "TAKEOVER"}, {"verification": "UNKNOWN"}),
        "text": "the model said FAILURE out loud",
        "count": 3,
    }

    assert sorted(tcode.codes_of(document)) == [
        "MODEL_TIMEOUT",
        "STUCK",
        "TAKEOVER",
        "UNKNOWN",
    ]


def test_codes_of_ignores_empty_and_non_string_values() -> None:
    document = {"outcome": None, "failure": "", "reason_code": 7, "status": "COMPLETED"}

    assert list(tcode.codes_of(document)) == []


def test_codes_of_walks_several_reports_at_once() -> None:
    first = {"runtime": {"outcome": "SUCCESS"}}
    second = {"runtime": {"results": [{"verification": "FAILURE"}]}}

    assert sorted(tcode.codes_of(first, second)) == ["FAILURE", "SUCCESS"]


def test_tally_ignores_blank_codes() -> None:
    report = tcode.tally(["", "OK"])

    assert report["total"] == 1
    assert report["by_code"] == {"OK": 1}
