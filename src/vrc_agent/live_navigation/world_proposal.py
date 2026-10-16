"""世界 v2 提案的离线加载/尺寸评估准备；不会生成生产 LiveWorld 或改变提示词。"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, model_validator

from vrc_agent.contracts import Contract, Entity

from .skill import CalibratedSkill
from .world import ArrivalRule, load_live_world


class LandmarkProposal(Contract):
    group: Literal["G3", "G5"]
    arrival: ArrivalRule
    prompt_addendum: Annotated[str, Field(min_length=1, max_length=2000)]
    # 未重拍的图不允许填旧哈希或零值冒充；真实摘要须由下一次发布契约接纳。
    reference_sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")] | None = None
    reference_state: Literal["PENDING_FIELD_CAPTURE"] = "PENDING_FIELD_CAPTURE"


class WorldV2Proposal(Contract):
    version: Literal["vrchat-home-live-v2-proposal"]
    status: Literal["PROPOSAL_NOT_APPROVED"]
    source_world_digest: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    changes: tuple[LandmarkProposal, ...]

    @model_validator(mode="after")
    def validate_scope(self):
        if self.source_world_digest != load_live_world().digest():
            raise ValueError("proposal source world mismatch")
        if len(self.changes) != 2 or {c.group for c in self.changes} != {"G3", "G5"}:
            raise ValueError("proposal must cover G3 and G5 only")
        if any(c.reference_sha256 is not None for c in self.changes):
            raise ValueError("pending proposal cannot claim captured reference")
        return self

    def evaluate_box(self, group: str, bbox: tuple[float, float, float, float]) -> bool:
        """仅评估提案的图像尺寸/居中条件，绝不等同任务 SUCCESS。"""
        change = next((c for c in self.changes if c.group == group), None)
        if change is None:
            raise ValueError("group outside proposal")
        landmark = load_live_world().landmark(group).model_copy(update={"arrival": change.arrival})
        entity = Entity(
            track_id="proposal",
            label=landmark.label,
            bbox=bbox,
            confidence=1.0,
            evidence_observation_id="offline-proposal",
            last_seen_ns=0,
        )
        return CalibratedSkill(landmark).arrived(entity)


def load_world_proposal(path: Path) -> WorldV2Proposal:
    """显式离线草案路径；生产入口仍只加载世界 v1，不接受此类型。"""
    return WorldV2Proposal.model_validate_json(path.read_text(encoding="utf-8"))
