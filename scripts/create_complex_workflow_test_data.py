from __future__ import annotations

from pathlib import Path

import shapefile


ROOT = Path(r"D:\Data\GeoPilotComplexTest")
INPUT_DIR = ROOT / "input"
OUTPUT_DIR = ROOT / "output"
DATASET = INPUT_DIR / "taihu_test_area"


FEATURES = [
    {
        "name": "Taihu_A",
        "city": "Wuxi",
        "zone": "LakeCore",
        "status": "raw",
        "score": 95,
        "note": "target"
    },
    {
        "name": "Taihu_B",
        "city": "Suzhou",
        "zone": "LakeCore",
        "status": "raw",
        "score": 86,
        "note": "candidate"
    },
    {
        "name": "Taihu_C",
        "city": "Huzhou",
        "zone": "Shore",
        "status": "raw",
        "score": 72,
        "note": "normal"
    },
    {
        "name": "Taihu_D",
        "city": "Changzhou",
        "zone": "Shore",
        "status": "raw",
        "score": 61,
        "note": "normal"
    },
]


POLYGONS = [
    [[119.90, 31.35], [120.08, 31.35], [120.08, 31.52], [119.90, 31.52], [119.90, 31.35]],
    [[120.12, 31.18], [120.32, 31.18], [120.32, 31.35], [120.12, 31.35], [120.12, 31.18]],
    [[119.72, 30.98], [119.92, 30.98], [119.92, 31.15], [119.72, 31.15], [119.72, 30.98]],
    [[120.00, 30.85], [120.20, 30.85], [120.20, 31.02], [120.00, 31.02], [120.00, 30.85]],
]


def main() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        path = DATASET.with_suffix(suffix)
        if path.exists():
            path.unlink()

    writer = shapefile.Writer(str(DATASET), shapeType=shapefile.POLYGON)
    writer.autoBalance = 1
    writer.field("NAME", "C", size=32)
    writer.field("CITY", "C", size=32)
    writer.field("ZONE", "C", size=32)
    writer.field("STATUS", "C", size=32)
    writer.field("SCORE", "N", size=8, decimal=0)
    writer.field("NOTE", "C", size=32)

    for feature, polygon in zip(FEATURES, POLYGONS):
        writer.poly([polygon])
        writer.record(
            feature["name"],
            feature["city"],
            feature["zone"],
            feature["status"],
            feature["score"],
            feature["note"]
        )
    writer.close()

    DATASET.with_suffix(".prj").write_text(
        'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",'
        'SPHEROID["WGS_1984",6378137.0,298.257223563]],'
        'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]',
        encoding="utf-8"
    )
    DATASET.with_suffix(".cpg").write_text("UTF-8", encoding="utf-8")

    print("created_shapefile=%s" % DATASET.with_suffix(".shp"))
    print("output_dir=%s" % OUTPUT_DIR)
    print("target_feature=NAME Taihu_A")
    print("edit_test=STATUS raw -> checked where NAME Taihu_A")


if __name__ == "__main__":
    main()
