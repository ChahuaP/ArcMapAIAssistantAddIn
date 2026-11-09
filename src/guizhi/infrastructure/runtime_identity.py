
"""Identify the exact source and installed runtime of a recorded execution."""
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import version
import sys

from guizhi.infrastructure.workspace import SOURCE_ROOT


def source_fingerprint(root):
    checksum = sha256()
    files = [root / "VERSION", root / "requirements.lock",
             *sorted((root / "src/guizhi").rglob("*.py"))]
    for path in files:
        checksum.update(path.relative_to(root).as_posix().encode())
        checksum.update(sha256(path.read_bytes()).digest())
    return checksum.hexdigest()


@lru_cache(maxsize=1)
def runtime_identity():
    return {"source_sha256": source_fingerprint(SOURCE_ROOT), "python": sys.version,
            "packages": {name: version(name) for name in
                         ("pydantic", "geopandas", "shapely", "pyogrio", "pyproj", "rasterio", "onnxruntime")}}
