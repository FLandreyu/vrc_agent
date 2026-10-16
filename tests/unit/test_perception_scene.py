"""感知闸门：不可信的模型载荷 → 受约束的 `SceneState`（纯函数，无供应商、无时钟）。

判据全部来自 ARCHITECTURE 第 3、4 节：模型只能描述**看到了什么**，看不准就是
`confidence=None` 的空场景；任何无法核实的实体一律丢弃并留痕。
"""

from __future__ import annotations

import json

import pytest

from vrc_agent.contracts import Observation, SceneState
from vrc_agent.perception.scene import (
    ACTION_LIKE_KEYS,
    MAX_HAZARDS,
    MAX_TEXT_CHARS,
    MAX_UNCERTAINTIES,
    build_scene,
    unknown_scene,
)

SESSION = "unit-scene"
OBSERVATION_ID = "obs-scene-1"
CAPTURED_AT_NS = 5_000_000_000
GENERATED_AT_NS = 5_100_000_000


def observation(**changes: object) -> Observation:
    payload: dict[str, object] = {
        "session_id": SESSION,
        "observation_id": OBSERVATION_ID,
        "captured_at_ns": CAPTURED_AT_NS,
        "environment_epoch": 1,
        "window_state": "visible",
        "frame_ref": "frame-scene-1",
        "frame_size": (64, 48),
    }
    payload.update(changes)
    return Observation(**payload)  # type: ignore[arg-type]


def entity_payload(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "track_id": "person_1",
        "label": "person",
        "bbox": [0.1, 0.1, 0.4, 0.6],
        "confidence": 0.8,
        "evidence_observation_id": OBSERVATION_ID,
    }
    payload.update(changes)
    return payload


def build(payload: object, **kwargs: object):
    return build_scene(
        payload,
        observation(),
        generated_at_ns=GENERATED_AT_NS,
        **kwargs,  # type: ignore[arg-type]
    )


def test_non_object_payload_becomes_an_unknown_scene() -> None:
    for payload in ("not json at all", [1, 2, 3], 17, None, b"bytes"):
        draft = build(payload)
        assert draft.unknown_reason == "payload_not_object"
        assert draft.scene.confidence is None
        assert draft.scene.entities == ()
        assert draft.scene.uncertainties != ()


def test_entities_are_kept_with_their_evidence_and_the_weakest_confidence() -> None:
    draft = build(
        {
            "entities": [
                entity_payload(track_id="a_1", confidence=0.9),
                entity_payload(track_id="b_2", confidence=0.42),
            ]
        }
    )
    assert draft.unknown_reason is None
    assert draft.dropped_entities == 0
    assert [entity.track_id for entity in draft.scene.entities] == ["a_1", "b_2"]
    assert all(entity.evidence_observation_id == OBSERVATION_ID for entity in draft.scene.entities)
    # 整体可信度是实体里最弱的一条，不是平均值、更不是最大值。
    assert draft.scene.confidence == pytest.approx(0.42)
    assert draft.scene.observation_ids == (OBSERVATION_ID,)
    assert draft.scene.session_id == SESSION
    assert draft.scene.environment_epoch == 1
    assert draft.scene.generated_at_ns == GENERATED_AT_NS


def test_entity_citing_another_observation_is_dropped() -> None:
    draft = build(
        {
            "entities": [
                entity_payload(track_id="good", evidence_observation_id=OBSERVATION_ID),
                entity_payload(track_id="bad", evidence_observation_id="obs-somewhere-else"),
            ]
        }
    )
    assert [entity.track_id for entity in draft.scene.entities] == ["good"]
    assert draft.dropped_entities == 1
    assert any("cites evidence" in text for text in draft.scene.uncertainties)


@pytest.mark.parametrize(
    "bbox",
    [
        [-0.01, 0.1, 0.4, 0.6],  # 越界（下界）
        [0.1, 0.1, 1.4, 0.6],  # 越界（上界）
        [0.5, 0.5, 0.2, 0.2],  # 顺序颠倒
        [0.1, 0.1, 0.4],  # 少一个数
        [0.1, 0.1, 0.4, 0.6, 0.7],  # 多一个数
        ["0.1", 0.1, 0.4, 0.6],  # 字符串
        [True, 0.1, 0.4, 0.6],  # 布尔
        [0.1, 0.1, float("nan"), 0.6],  # 非有限
        "0.1,0.1,0.4,0.6",  # 整串字符串
        None,
    ],
)
def test_malformed_bbox_is_dropped(bbox: object) -> None:
    draft = build({"entities": [entity_payload(bbox=bbox)]})
    assert draft.scene.entities == ()
    assert draft.scene.confidence is None
    assert draft.dropped_entities == 1
    assert draft.unknown_reason == "no_usable_entity"


@pytest.mark.parametrize("confidence", [1.5, -0.01, "high", True, None, float("inf")])
def test_unusable_confidence_is_dropped(confidence: object) -> None:
    draft = build({"entities": [entity_payload(confidence=confidence)]})
    assert draft.scene.entities == ()
    assert draft.dropped_entities == 1


@pytest.mark.parametrize(
    "track_id",
    [
        "",
        42,
        "has space",
        "has/slash",
        "has\\backslash",
        "a" * 129,  # 超过 Identifier 的 128 字符上限
        "emoji🙂",
    ],
)
def test_unsafe_track_id_is_dropped(track_id: object) -> None:
    draft = build({"entities": [entity_payload(track_id=track_id)]})
    assert draft.scene.entities == ()
    assert draft.dropped_entities == 1


def test_entities_beyond_the_limit_are_counted_as_overflow() -> None:
    payload = {"entities": [entity_payload(track_id=f"track_{index}") for index in range(5)]}
    draft = build(payload, max_entities=2)
    assert len(draft.scene.entities) == 2
    assert draft.overflow_entities == 3
    assert draft.dropped_entities == 0
    assert any("limit" in text for text in draft.notes)


def test_a_payload_without_entities_is_unknown_but_keeps_the_notes() -> None:
    draft = build({"uncertainties": ["frame too dark to read"], "hazards": ["low light"]})
    assert draft.unknown_reason == "no_usable_entity"
    assert draft.scene.confidence is None
    assert draft.scene.entities == ()
    assert "frame too dark to read" in draft.scene.uncertainties
    assert draft.scene.hazards == ("low light",)


def test_an_explanation_is_always_present_even_when_the_model_gives_none() -> None:
    # 合法但什么都没说的载荷（空实体、无 uncertainties）：下游仍要能读到「为什么不知道」。
    draft = build({"entities": [], "hazards": [], "uncertainties": []})
    assert draft.scene.confidence is None
    assert draft.scene.entities == ()
    assert draft.scene.uncertainties == ("model returned no usable entity; scene is unknown",)


def test_action_like_fields_are_ignored_and_never_reach_the_scene() -> None:
    payload = {
        "entities": [entity_payload()],
        "lease_id": "lease-should-not-exist",
        "expires_at_ns": 999,
        "forward": 1.0,
        "tool": {"name": "osculator", "command": "send /input/Jump 1"},
        "nested": {"path": "C:/secret/place"},
    }
    draft = build(payload)
    assert draft.action_like_fields
    assert set(draft.action_like_fields) <= ACTION_LIKE_KEYS | {"expires_at_ns"}
    dumped = draft.scene.model_dump_json()
    document = json.loads(dumped)

    def keys_of(node: object) -> set[str]:
        found: set[str] = set()
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(str(key))
                found |= keys_of(value)
        elif isinstance(node, list):
            for item in node:
                found |= keys_of(item)
        return found

    # 字段名不得成为场景的字段（值更不得出现）；字段名只允许作为描述性文本留痕。
    assert not (keys_of(document) & ACTION_LIKE_KEYS)
    for value in ("lease-should-not-exist", "osculator", "send /input/Jump 1", "C:/secret/place"):
        assert value not in dumped
    assert any("ignored action-like field" in text for text in draft.scene.uncertainties)
    # 场景仍然只包含实体与描述性文本。
    assert json.loads(dumped).keys() <= {
        "schema_version",
        "event_id",
        "session_id",
        "scene_id",
        "observation_ids",
        "generated_at_ns",
        "environment_epoch",
        "entities",
        "hazards",
        "uncertainties",
        "active_goal",
        "confidence",
    }


def test_uncertainties_are_capped_and_unusable_items_are_dropped() -> None:
    payload = {
        "entities": [entity_payload()],
        "uncertainties": [f"note {index}" for index in range(MAX_UNCERTAINTIES + 5)]
        + [None, 17, "   "],
    }
    draft = build(payload)
    assert len(draft.scene.uncertainties) <= MAX_UNCERTAINTIES
    assert draft.scene.uncertainties[0] == "note 0"


def test_hazards_are_capped() -> None:
    payload = {
        "entities": [entity_payload()],
        "hazards": [f"hazard {index}" for index in range(MAX_HAZARDS + 3)],
    }
    draft = build(payload)
    assert len(draft.scene.hazards) == MAX_HAZARDS


def test_descriptive_text_is_cleaned_and_truncated() -> None:
    draft = build(
        {
            "entities": [entity_payload()],
            "active_goal": "  walk   to\n the   mirror  " + "x" * 400,
        }
    )
    goal = draft.scene.active_goal
    assert goal is not None
    assert "\n" not in goal and "  " not in goal
    assert len(goal) <= MAX_TEXT_CHARS


def test_last_seen_defaults_to_the_capture_time() -> None:
    draft = build({"entities": [entity_payload()]})
    assert draft.scene.entities[0].last_seen_ns == CAPTURED_AT_NS

    stamped = build({"entities": [entity_payload(last_seen_ns=CAPTURED_AT_NS + 7)]})
    assert stamped.scene.entities[0].last_seen_ns == CAPTURED_AT_NS + 7


def test_unknown_scene_is_empty_and_explains_itself() -> None:
    scene = unknown_scene(observation(), reason="window_state=minimized", generated_at_ns=1)
    assert isinstance(scene, SceneState)
    assert scene.confidence is None
    assert scene.entities == ()
    assert scene.observation_ids == (OBSERVATION_ID,)
    assert scene.uncertainties == ("window_state=minimized",)


def test_scenes_survive_a_strict_round_trip() -> None:
    for payload in (
        {"entities": [entity_payload()]},
        {"entities": []},
        "not an object",
        {"entities": [entity_payload(bbox=[0.9, 0.9, 0.1, 0.1])]},
    ):
        scene = build(payload).scene
        again = SceneState.model_validate(scene.model_dump())
        assert again == scene
        assert scene.model_dump()["environment_epoch"] == 1
        # frozen：不可变，防止下游偷偷改写场景。
        with pytest.raises(Exception):
            scene.confidence = 1.0  # type: ignore[misc]


def test_max_entities_must_be_positive() -> None:
    with pytest.raises(ValueError):
        build({"entities": []}, max_entities=0)
