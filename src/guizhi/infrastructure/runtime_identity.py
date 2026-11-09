
"""Identify the exact source and installed runtime of a recorded execution."""
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import version
import sys

from guizhi.infrastructure.workspace import SOURCE_ROOT


@lru_cache(maxsize=1)
def runtime_identity():
    checksum = sha256()
    files = [SOURCE_ROOT / "VERSION", SOURCE_ROOT / "requirements.lock",
             *sorted((SOURCE_ROOT / "src/guizhi").rglob("*.py"))]
    for path in files:
        checksum.update(path.relative_to(SOURCE_ROOT).as_posix().encode())
        checksum.update(sha256(path.read_bytes()).digest())
    return {"source_sha256": checksum.hexdigest(), "python": sys.version,
            "packages": {name: version(name) for name in
                         ("pydantic", "geopandas", "shapely", "pyogrio", "pyproj", "rasterio", "onnxruntime")}}
