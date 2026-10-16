"""T04 回放源：顺序稳定、逐次可复现、耗尽后不循环。

这是验收条件 2 的落点。回放用**虚拟时钟**推进（不是真等 1.8 秒），所以「两次运行
逐字节相同」这句话是可以被机器验证的：只要序列摘要相同，事件顺序、时间戳、帧引用
和帧内容摘要就全都相同。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from vrc_agent.capture.errors import ReplayExhausted
from vrc_agent.capture.replay import (
    ReplaySource,
    collect,
    default_replay_source,
    load_fixture,
    observation_signature,
    parse_fixture,
    sequence_digest,
)
from vrc_agent.capture.synthetic import DEFAULT_FIXTURE, default_fixture

ARCHIVE = Path(__file__).with_name("t04_smoke.json")

#: 黄金值：这条素材跑出来的序列摘要。任何改动（步骤顺序、at_ms、图案规格、帧引用
#: 命名、观测 id 生成方式）都会让它变化——变化本身不是错，但要有人为它负责。
GOLDEN_DIGEST = "fadc5ee10ccf227abea0ff6cf8374073919e94aed94878c2a38f08de7786e22a"

EXPECTED_STATES = (
    "visible",
    "visible",
    "visible",
    "unknown",  # 黑屏
    "minimized",
    "lost",  # 窗口消失
    "visible",
    "unknown",  # 遮挡
    "unknown",  # GDI 失败
    "lost",  # 隐藏
    "unknown",  # 空尺寸
    "unknown",  # 超限
    "visible",
)


# --------------------------------------------------------------------------
# 存档素材与内置素材
# --------------------------------------------------------------------------


def test_archive_matches_the_builtin_fixture() -> None:
    assert json.loads(ARCHIVE.read_text(encoding="utf-8")) == default_fixture()
    assert default_fixture() == DEFAULT_FIXTURE


def test_archive_and_builtin_produce_the_same_sequence() -> None:
    from_file = sequence_digest(collect(ReplaySource(load_fixture(ARCHIVE))))
    from_code = sequence_digest(collect(default_replay_source()))
    assert from_file == from_code == GOLDEN_DIGEST


def test_sequence_matches_the_documented_states() -> None:
    states = tuple(observation.window_state for observation, _ in collect(default_replay_source()))
    assert states == EXPECTED_STATES


# --------------------------------------------------------------------------
# 验收条件 2：回放可稳定复现事件顺序
# --------------------------------------------------------------------------


def test_two_runs_are_byte_identical() -> None:
    first = collect(default_replay_source())
    second = collect(default_replay_source())
    assert [observation_signature(*record) for record in first] == [
        observation_signature(*record) for record in second
    ]
    assert sequence_digest(first) == sequence_digest(second) == GOLDEN_DIGEST


def test_two_runs_share_no_mutable_state() -> None:
    first = collect(default_replay_source())
    second = collect(default_replay_source())
    for (left, left_frame), (right, right_frame) in zip(first, second, strict=True):
        assert left is not right
        if left_frame is not None and right_frame is not None:
            assert left_frame is not right_frame
            assert left_frame.data == right_frame.data


def test_event_order_follows_the_fixture_and_time_never_goes_backwards() -> None:
    source = default_replay_source()
    records = collect(source)
    assert len(records) == len(source.fixture.steps)

    stamps = [observation.captured_at_ns for observation, _ in records]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)

    # 虚拟时钟按 at_ms 推进：时间戳差值必须与素材里的 at_ms 差值完全一致。
    base = source.fixture.start_ns
    offsets = [(stamp - base) // 1_000_000 for stamp in stamps]
    assert offsets == [step.at_ms for step in source.fixture.steps]


def test_replay_does_not_wait_in_real_time() -> None:
    started = time.monotonic()
    collect(default_replay_source())
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"回放用了真实时间 {elapsed:.3f}s，说明它没有走虚拟时钟"


def test_frame_references_follow_a_stable_scheme() -> None:
    refs = [
        observation.frame_ref
        for observation, _ in collect(default_replay_source())
        if observation.frame_ref is not None
    ]
    assert refs == [f"replay.t04_smoke.{index:04d}" for index in (1, 2, 3, 4, 5)]


def test_observations_are_generated_not_replayed_from_disk() -> None:
    """素材描述的是窗口状态，失败结论仍由产品代码判定。"""
    source = default_replay_source()
    observation = source.next_observation()
    assert observation.observation_id != source.fixture.session_id
    assert not observation.observation_id.startswith("replay.t04_smoke.")
    assert observation.window_state == "visible"


# --------------------------------------------------------------------------
# 耗尽行为：不循环、不填充、不重复
# --------------------------------------------------------------------------


def test_exhaustion_is_explicit_and_sticky() -> None:
    source = default_replay_source()
    seen = []
    while True:
        try:
            seen.append(source.next_observation().observation_id)
        except ReplayExhausted as exc:
            assert source.fixture.name in str(exc)
            break
    assert len(seen) == len(source.fixture.steps)
    assert source.remaining == 0

    for _ in range(3):
        with pytest.raises(ReplayExhausted):
            source.next_observation()
    assert collect(source) == []
    assert source.remaining == 0


def test_remaining_counts_down_and_limit_stops_early() -> None:
    source = default_replay_source()
    assert source.remaining == len(source.fixture.steps)
    source.next_observation()
    assert source.remaining == len(source.fixture.steps) - 1

    head = collect(default_replay_source(), limit=3)
    assert [observation.window_state for observation, _ in head] == ["visible"] * 3


def test_stats_accumulate_over_a_full_run() -> None:
    source = default_replay_source()
    collect(source)
    stats = source.stats()
    assert stats.attempts == len(source.fixture.steps)
    assert stats.ok == 5
    assert dict(stats.failures) == {
        "FRAME_BLACK": 1,
        "WINDOW_MINIMIZED": 1,
        "WINDOW_NOT_FOUND": 1,
        "WINDOW_OCCLUDED": 1,
        "CAPTURE_FAILED": 1,
        "WINDOW_HIDDEN": 1,
        "WINDOW_EMPTY_SIZE": 1,
        "WINDOW_TOO_LARGE": 1,
    }
    # 64x48 → 96x72（第 2 步）→ 64x48（最后一步恢复）：只统计真正抓到帧的尺寸变化。
    assert stats.resolution_changes == 2
    assert stats.last_size == (64, 48)
    assert stats.frames_held == 5
    assert stats.evicted_frames == 0


def test_closing_releases_the_frames() -> None:
    source = default_replay_source()
    collect(source, limit=1)
    assert len(source.frames) == 1
    source.close()
    assert len(source.frames) == 0
    with pytest.raises(RuntimeError):
        source.next_observation()


# --------------------------------------------------------------------------
# 素材解析：坏素材必须当场拒绝，而不是跑出半条序列
# --------------------------------------------------------------------------


def _minimal(**changes: object) -> dict:
    data: dict = {"steps": [{"at_ms": 0, "window": "minimized"}]}
    data.update(changes)
    return data


def test_minimal_fixture_defaults() -> None:
    fixture = parse_fixture(_minimal())
    assert fixture.name == "fixture"
    assert fixture.session_id == "replay-fixture"
    assert fixture.window_title == "VRChat"
    assert fixture.window_class == "UnityWndClass"
    assert fixture.pid == 0
    assert fixture.dpi == 96
    assert fixture.start_ns == 1_000_000_000
    assert fixture.environment_epoch == 0
    assert fixture.occlusion_samples == 5
    assert fixture.steps[0].window == "minimized"


def test_source_without_a_clock_starts_at_the_fixture_start() -> None:
    fixture = parse_fixture(default_fixture())
    source = ReplaySource(fixture)
    assert source.next_observation().captured_at_ns == fixture.start_ns


@pytest.mark.parametrize(
    ("data", "message"),
    [
        pytest.param([], "JSON object", id="not-an-object"),
        pytest.param(_minimal(steps=[]), "non-empty steps", id="no-steps"),
        pytest.param(_minimal(steps="visible"), "non-empty steps", id="steps-not-a-list"),
        pytest.param(_minimal(steps=["visible"]), "must be a mapping", id="step-not-a-mapping"),
        pytest.param(_minimal(window=[]), "window must be a mapping", id="window-not-a-mapping"),
        pytest.param(_minimal(name="../etc"), "simple slug", id="bad-name"),
        pytest.param(_minimal(name=""), "simple slug", id="empty-name"),
        pytest.param(
            _minimal(occlusion_samples=0), "occlusion_samples", id="zero-occlusion-samples"
        ),
        pytest.param(
            _minimal(steps=[{"at_ms": 10, "window": "gone"}, {"at_ms": 5, "window": "gone"}]),
            "moves backwards",
            id="backwards-time",
        ),
        pytest.param(
            _minimal(steps=[{"at_ms": 0, "window": "invisible"}]),
            "unknown window condition",
            id="unknown-condition",
        ),
        pytest.param(
            _minimal(steps=[{"at_ms": 0, "window": "minimized", "failure": "WINDOW_MINIMIZED"}]),
            "cannot inject failure",
            id="failure-outside-the-injectable-set",
        ),
        pytest.param(
            _minimal(steps=[{"at_ms": 0, "window": "gone", "failure": "CAPTURE_FAILED"}]),
            None,
            id="injectable-failure-is-allowed",
        ),
        pytest.param(
            _minimal(steps=[{"at_ms": 0, "window": "visible", "pattern": {"type": "solid"}}]),
            None,
            id="visible-with-pattern-is-fine",
        ),
    ],
)
def test_bad_fixtures_are_rejected(data: object, message: str | None) -> None:
    if message is None:
        parse_fixture(data)
        return
    with pytest.raises(ValueError, match=message):
        parse_fixture(data)


@pytest.mark.parametrize(
    "step",
    [
        pytest.param({"at_ms": -1, "window": "gone"}, id="negative-at_ms"),
        pytest.param({"at_ms": 1.5, "window": "gone"}, id="float-at_ms"),
        pytest.param({"at_ms": True, "window": "gone"}, id="bool-at_ms"),
        pytest.param({"window": "gone"}, id="missing-at_ms"),
        pytest.param({"at_ms": 0}, id="missing-window"),
        pytest.param({"at_ms": 0, "window": "visible"}, id="visible-without-pattern"),
        pytest.param({"at_ms": 0, "window": "visible", "pattern": []}, id="pattern-not-a-map"),
        pytest.param(
            {"at_ms": 0, "window": "visible", "pattern": {"type": "solid"}, "size": [1]},
            id="size-too-short",
        ),
        pytest.param(
            {"at_ms": 0, "window": "visible", "pattern": {"type": "solid"}, "size": [0, 4]},
            id="zero-size",
        ),
        pytest.param(
            {
                "at_ms": 0,
                "window": "visible",
                "pattern": {"type": "solid"},
                "occlusion_ratio": 1.5,
            },
            id="occlusion-out-of-range",
        ),
        pytest.param(
            {
                "at_ms": 0,
                "window": "visible",
                "pattern": {"type": "solid"},
                "occlusion_ratio": True,
            },
            id="occlusion-bool",
        ),
    ],
)
def test_bad_steps_are_rejected(step: dict) -> None:
    with pytest.raises(ValueError):
        parse_fixture(_minimal(steps=[step]))


def test_fixture_steps_are_immutable() -> None:
    fixture = parse_fixture(default_fixture())
    assert isinstance(fixture.steps, tuple)
    with pytest.raises(Exception):
        fixture.steps[0].at_ms = 99  # type: ignore[misc]
