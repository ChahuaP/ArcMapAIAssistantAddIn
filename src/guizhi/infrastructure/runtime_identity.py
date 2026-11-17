
"""Identify the exact source and installed runtime of a recorded execution."""
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
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
def base_identity():
    packages = {name: version(name) for name in ("pydantic", "geopandas", "shapely", "pyogrio", "pyproj")}
    return {"source_sha256": source_fingerprint(SOURCE_ROOT), "python": sys.version, "packages": packages}


def runtime_identity():
    base = base_identity()
    packages = dict(base["packages"])
    for name in ("rasterio", "onnxruntime", "rapidocr-onnxruntime"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            pass
    return {**base, "packages": packages}
