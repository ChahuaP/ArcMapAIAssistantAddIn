"""Local execution environment facts."""
import sys

import pyogrio
import shapely


def health(ctx):
    with ctx.workspace.connect() as db:
        version = db.execute("SELECT sqlite_version()").fetchone()[0]
        rtree = bool(db.execute("SELECT sqlite_compileoption_used('ENABLE_RTREE')").fetchone()[0])
    return {"health": {"mode": "independent", "python": sys.version.split()[0], "gdal": pyogrio.__gdal_version_string__,
                       "geos": shapely.geos_version_string, "sqlite": version,
                       "rtree": rtree}}
