from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class CleanupResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    deleted: tuple[str, ...] = ()
    skipped_active: tuple[str, ...] = ()
    refused: tuple[str, ...] = ()
    dry_run: bool
    disabled_reason: str | None = None


def _safe_candidate(root: Path, value: str | Path) -> Path | None:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    if ".." in relative.parts:
        return None
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return None
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def cleanup_paths(
    root: str | Path,
    candidates: Sequence[str | Path],
    *,
    active: Sequence[str | Path] = (),
    dry_run: bool = True,
) -> CleanupResult:
    supplied_root = Path(root)
    if supplied_root.is_symlink():
        raise ValueError("Cleanup root must be an existing non-symlink directory")
    approved_root = supplied_root.resolve(strict=True)
    if not approved_root.is_dir():
        raise ValueError("Cleanup root must be an existing non-symlink directory")
    active_paths = {
        safe
        for value in active
        if (safe := _safe_candidate(approved_root, value)) is not None
    }
    deleted: list[str] = []
    skipped: list[str] = []
    refused: list[str] = []
    for value in dict.fromkeys(map(str, candidates)):
        candidate = _safe_candidate(approved_root, value)
        if candidate is None or candidate == approved_root:
            refused.append(value)
            continue
        relative = str(candidate.relative_to(approved_root))
        if any(active == candidate or active.is_relative_to(candidate) for active in active_paths):
            skipped.append(relative)
            continue
        if not candidate.exists():
            continue
        if dry_run:
            deleted.append(relative)
            continue
        if candidate.is_dir():
            shutil.rmtree(candidate)
        else:
            candidate.unlink()
        deleted.append(relative)
    return CleanupResult(
        deleted=tuple(deleted),
        skipped_active=tuple(skipped),
        refused=tuple(refused),
        dry_run=dry_run,
    )


def durable_cleanup_disabled(*, dry_run: bool = True) -> CleanupResult:
    return CleanupResult(
        dry_run=dry_run,
        disabled_reason="Durable retention period is pending user approval.",
    )
