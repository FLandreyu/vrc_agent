"""结构化日志的保留策略。

默认**只列不删**（`apply=False`）：删除是显式动作，报告里写清楚候选、体积和错误。
范围限制很窄——只处理目标目录下**后缀匹配的普通文件**，不进子目录、不跟随符号链接。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

LOG_SUFFIX = ".jsonl"
DEFAULT_KEEP_DAYS = 14


@dataclass(frozen=True, slots=True)
class PruneEntry:
    path: Path
    size_bytes: int
    modified_at: str
    age_days: float

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.path.name,
            "size_bytes": self.size_bytes,
            "modified_at": self.modified_at,
            "age_days": self.age_days,
        }


def plan_prune(
    directory: Path,
    *,
    keep_days: int = DEFAULT_KEEP_DAYS,
    suffix: str = LOG_SUFFIX,
    now: datetime | None = None,
) -> list[PruneEntry]:
    """列出超过保留期的候选文件；按修改时间从旧到新排序。不存在的目录返回空列表。"""
    if keep_days < 0:
        raise ValueError("keep_days must be >= 0")
    directory = Path(directory)
    if not directory.is_dir():
        return []
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=keep_days)
    entries: list[PruneEntry] = []
    for path in sorted(directory.glob(f"*{suffix}")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if modified >= cutoff:
            continue
        entries.append(
            PruneEntry(
                path=path,
                size_bytes=stat.st_size,
                modified_at=modified.isoformat(),
                age_days=round((moment - modified).total_seconds() / 86_400, 3),
            )
        )
    return sorted(entries, key=lambda entry: entry.modified_at)


def prune_logs(
    directory: Path,
    *,
    keep_days: int = DEFAULT_KEEP_DAYS,
    apply: bool = False,
    suffix: str = LOG_SUFFIX,
    now: datetime | None = None,
) -> dict[str, object]:
    """按年龄清理日志。`apply=False`（默认）时只报告将要删除的文件。"""
    candidates = plan_prune(directory, keep_days=keep_days, suffix=suffix, now=now)
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    freed = 0
    if apply:
        for entry in candidates:
            try:
                entry.path.unlink()
            except OSError as exc:
                errors.append({"name": entry.path.name, "error": type(exc).__name__})
                continue
            deleted.append(entry.path.name)
            freed += entry.size_bytes
    return {
        "directory": str(Path(directory)),
        "keep_days": keep_days,
        "applied": bool(apply),
        "candidates": [entry.as_dict() for entry in candidates],
        "candidate_bytes": sum(entry.size_bytes for entry in candidates),
        "deleted": deleted,
        "freed_bytes": freed,
        "errors": errors,
        "note": (
            "deletions are permanent; only plain files in this directory are touched"
            if apply
            else "dry run: nothing was deleted, pass --apply to delete the candidates"
        ),
    }


__all__ = ["DEFAULT_KEEP_DAYS", "LOG_SUFFIX", "PruneEntry", "plan_prune", "prune_logs"]
