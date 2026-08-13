"""Exact Git working-tree provenance for frozen experiment campaigns."""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple


@dataclass(frozen=True)
class RepositoryProvenance:
    head: str
    clean: bool
    tracked_diff_sha256: str
    untracked: Tuple[Tuple[str, str], ...]
    digest: str

    def as_dict(self):
        return {"head": self.head, "clean": self.clean,
                "tracked_diff_sha256": self.tracked_diff_sha256,
                "untracked": [{"path": path, "sha256": digest} for path, digest in self.untracked],
                "digest": self.digest}


def repository_state(repository: Path) -> RepositoryProvenance:
    repository = repository.resolve()
    head = _git(repository, "rev-parse", "HEAD").decode("ascii").strip()
    diff = _git(repository, "diff", "--binary", "HEAD", "--", ".")
    untracked = [
        item for item in _git(repository, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
        if item
    ]
    untracked_documents = []
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
        member_digest = hashlib.sha256()
        digest.update(encoded_path)
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
                member_digest.update(chunk)
        digest.update(b"\0")
        untracked_documents.append((relative, member_digest.hexdigest()))
    return RepositoryProvenance(
        head=head, clean=not bool(diff or untracked),
        tracked_diff_sha256=hashlib.sha256(diff).hexdigest(),
        untracked=tuple(sorted(untracked_documents)), digest=digest.hexdigest())


def _git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments], cwd=repository, capture_output=True, check=True,
    ).stdout
