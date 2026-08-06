"""Identity-safe lifecycle operations for paired G2/G3 workspaces."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


class PairWorkspaceError(RuntimeError):
    pass


def _is_reparse_point(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.stat(), "st_file_attributes", 0) & 0x400)


def _pair_child(output_root: Path, candidate: Path) -> tuple[Path, Path, Path]:
    root = output_root.resolve()
    pair_root = root / "pair-work"
    child = candidate.absolute()
    if child in (root, pair_root) or child.parent != pair_root:
        raise PairWorkspaceError("pair-work target is not an identity-confirmed child directory.")
    for path in (root, pair_root, child):
        if path.exists() and _is_reparse_point(path):
            raise PairWorkspaceError("pair-work must not traverse a symlink or reparse point.")
    resolved_pair_root = pair_root.resolve()
    resolved_child = child.resolve()
    if resolved_child == resolved_pair_root or resolved_child.parent != resolved_pair_root:
        raise PairWorkspaceError("pair-work resolved outside its direct child boundary.")
    return root, pair_root, child


def reset_pair_workspace(output_root: Path, pair_work: Path) -> None:
    _, _, child = _pair_child(output_root, pair_work)
    if child.exists():
        shutil.rmtree(str(child))
    child.mkdir(parents=True)


def remove_pair_workspace(output_root: Path, pair_work: Path) -> None:
    reset_pair_workspace(output_root, pair_work)
    shutil.rmtree(str(pair_work.absolute()))


def relocate_pair_workspace(output_root: Path, source: Path, target: Path) -> None:
    """Publish an unlocked pair workspace as one permanent result directory."""
    root, pair_root, source = _pair_child(output_root, source)
    target = target.absolute()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PairWorkspaceError("pair output target escapes experiment output root.") from exc
    if target in (root, pair_root) or pair_root in target.parents:
        raise PairWorkspaceError("pair output target must be a permanent archive outside pair-work.")
    if not source.is_dir() or _is_reparse_point(source):
        raise PairWorkspaceError("pair output source must be a plain directory.")
    if target.exists():
        raise PairWorkspaceError("Experiment archive already exists: %s" % target)
    target.parent.mkdir(parents=True, exist_ok=True)
    parent = target.parent
    while True:
        if parent.exists() and _is_reparse_point(parent):
            raise PairWorkspaceError("pair output target must not traverse a symlink or reparse point.")
        if parent == root:
            break
        if parent == parent.parent:
            raise PairWorkspaceError("pair output target escapes experiment output root.")
        parent = parent.parent
    os.replace(str(source), str(target))
