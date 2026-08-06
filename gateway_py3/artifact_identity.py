"""Canonical identity for declared and executable output artifacts."""
from __future__ import annotations


_FORMAT_SUFFIXES = {
    "csv": (".csv",),
    "kmz": (".kmz",),
    "pdf": (".pdf",),
    "png": (".png",),
    "shp": (".shp",),
    "tif": (".tif", ".tiff"),
    "tiff": (".tif", ".tiff"),
}


def canonical_artifact_name(name: str | None, artifact_format: str | None) -> str | None:
    """Return the format-aware basename used to bind a task output to a step output."""
    if not isinstance(name, str) or not name.strip():
        return None
    basename = name.strip().replace("\\", "/").rsplit("/", 1)[-1]
    lowered = basename.casefold()
    for suffix in _FORMAT_SUFFIXES.get(str(artifact_format or "").casefold(), ()):
        if lowered.endswith(suffix) and len(basename) > len(suffix):
            basename = basename[:-len(suffix)]
            break
    return basename.casefold()


def artifact_format_suffixes(artifact_format: str | None) -> tuple[str, ...]:
    """Return the closed filename suffix set for a user-visible artifact format."""
    return _FORMAT_SUFFIXES.get(str(artifact_format or "").casefold(), ())


def artifact_filename_candidates(
    name: str | None, artifact_format: str | None,
) -> tuple[str, ...]:
    """Return canonical filenames that can bind a declared output name."""
    basename = canonical_artifact_name(name, artifact_format)
    if basename is None:
        return ()
    return tuple(basename + suffix for suffix in artifact_format_suffixes(artifact_format))


def _contains_token(text: str, token: str, require_left_boundary: bool) -> bool:
    cursor = 0
    while True:
        index = text.find(token, cursor)
        if index < 0:
            return False
        end = index + len(token)
        left = text[index - 1] if index else ""
        right = text[end] if end < len(text) else ""
        left_ok = not require_left_boundary or not left or not (
            left.isalnum() or left in "._-"
        )
        right_ok = not right or not (right.isalnum() or right in "._-")
        if left_ok and right_ok:
            return True
        cursor = index + 1


def artifact_filename_is_mentioned(
    name: str | None, artifact_format: str | None, evidence: str,
) -> bool:
    """Match a complete filename token, never a suffix of another filename."""
    normalized = str(evidence or "").replace("\\", "/").casefold()
    return any(
        _contains_token(normalized, candidate, require_left_boundary=True)
        for candidate in artifact_filename_candidates(name, artifact_format)
    )


def artifact_format_is_mentioned(artifact_format: str | None, evidence: str) -> bool:
    """Return whether evidence contains a complete filename suffix of this format."""
    normalized = str(evidence or "").casefold()
    return any(
        _contains_token(normalized, suffix, require_left_boundary=False)
        for suffix in artifact_format_suffixes(artifact_format)
    )
