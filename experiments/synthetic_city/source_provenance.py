"""Exact Git working-tree provenance for frozen experiment campaigns."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def repository_state(repository: Path) -> tuple[str, bool, str]:
    repository = repository.resolve()
    head = _git(repository, "rev-parse", "HEAD").decode("ascii").strip()
    diff = _git(repository, "diff", "--binary", "HEAD", "--", ".")
    untracked = [
        item for item in _git(repository, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if item
    ]
    digest = hashlib.sha256()
    digest.update(head.encode("ascii"))
    digest.update(b"\0tracked-diff\0")
    digest.update(diff)
    digest.update(b"\0untracked\0")
    for encoded_path in sorted(untracked):
        relative = encoded_path.decode("utf-8")
        path = (repository / relative).resolve()
        try:
            path.relative_to(repository)
        except ValueError as exc:
            raise RuntimeError("Untracked Git path escapes the repository.") from exc
        if not path.is_file():
            continue
        digest.update(encoded_path)
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return head, bool(diff or untracked), digest.hexdigest()


def _git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments], cwd=repository, capture_output=True, check=True,
    ).stdout
