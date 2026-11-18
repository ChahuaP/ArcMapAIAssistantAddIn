
"""Classify local multispectral imagery and compare verified categorical grids."""
from datetime import date
from hashlib import sha256
from math import isclose

import geopandas as gpd
import numpy as np
from pyproj import CRS
from shapely import make_valid
from shapely.geometry import shape

from guizhi.domain.imagery import Classification, ModelProfile
from guizhi.infrastructure.operations.report import publish_report
from guizhi.infrastructure.optional_components import require


MAX_CELLS = 4_000_000
MAX_POLYGONS = 50_000
CLASS_NODATA = 255
CHANGE_NODATA = 65535
CHANGE_UNKNOWN = 65534


def check_grid(src, model):
    grid = src.transform
    crs = CRS.from_user_input(src.crs)
    if (src.crs.to_epsg() != model.epsg or not crs.is_projected or
            any(axis.unit_conversion_factor != 1 for axis in crs.axis_info[:2]) or
            grid.b or grid.d or grid.a <= 0 or grid.e >= 0 or
            not all(isclose(value, model.resolution_m, rel_tol=1e-8) for value in src.res)):
        raise ValueError("Imagery is outside the model CRS or resolution; supply an aligned metre grid")
    x1, y1, x2, y2 = model.bounds
    left, bottom, right, top = src.bounds
    if not (x1 <= left < right <= x2 and y1 <= bottom < top <= y2):
        raise ValueError("Imagery footprint is outside the model geographic extent")
    if src.width * src.height > MAX_CELLS:
        raise ValueError("Imagery exceeds the per-scene processing limit of 4 million pixels")


def session_for(path, model):
    require("imagery")
    import onnxruntime as ort

    data = path.read_bytes()
    if sha256(data).hexdigest() != model.sha256:
        raise ValueError("ONNX model checksum differs from its profile")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.use_deterministic_compute = True
    session = ort.InferenceSession(data, sess_options=options, providers=["CPUExecutionProvider"])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if (len(inputs) != 1 or inputs[0].name != "features" or inputs[0].type != "tensor(float)" or
            len(inputs[0].shape) != 2 or isinstance(inputs[0].shape[0], int) or
            inputs[0].shape[1] != len(model.bands) or
            len(outputs) != 1 or outputs[0].name != "probabilities" or outputs[0].type != "tensor(float)" or
            len(outputs[0].shape) != 2 or outputs[0].shape[1] != len(model.classes)):
        raise ValueError("ONNX must accept float32 features and return one probability per declared class")
    return session


def classify_window(ctx, src, window, session, model):
    pixels = src.read(window=window, masked=True, out_dtype="float32").filled(np.nan)
    pixels = np.moveaxis(pixels, 0, -1).reshape(-1, len(model.bands))
    valid = np.isfinite(pixels).all(axis=1)
    result = np.full(len(pixels), CLASS_NODATA, dtype="uint8")
    result[valid] = 0
    with np.errstate(over="ignore", invalid="ignore"):
        pixels *= model.scale
    candidates = np.flatnonzero(valid & ((pixels >= model.sample_min) & (pixels <= model.sample_max)).all(axis=1))
    for start in range(0, len(candidates), 16384):
        ctx.check()
        subset = candidates[start:start + 16384]
        scores, = session.run(["probabilities"], {"features": pixels[subset]})
        if (scores.shape != (len(subset), len(model.classes)) or not np.isfinite(scores).all() or
                np.any((scores < 0) | (scores > 1)) or not np.allclose(scores.sum(axis=1), 1, atol=1e-5, rtol=0)):
            raise ValueError("ONNX returned invalid classification probabilities")
        best = scores.argmax(axis=1)
        confident = ((scores[np.arange(len(best)), best] >= model.min_confidence) &
                     ((scores == scores.max(axis=1, keepdims=True)).sum(axis=1) == 1))
        result[subset[confident]] = best[confident] + 1
    return result.reshape(int(window.height), int(window.width))


def profile_for(src, dtype, nodata):
    return dict(driver="GTiff", width=src.width, height=src.height, count=1, dtype=dtype,
                crs=src.crs, transform=src.transform, nodata=nodata,
                compress="deflate", tiled=True, blockxsize=256, blockysize=256)


def raster_receipt(ctx, path, expected, profile):
    from guizhi.infrastructure.operations.raster_backend import rio

    ctx.check()
    with rio.open(path) as src:
        if (src.crs != profile["crs"] or src.transform != profile["transform"] or
                src.nodata != profile["nodata"] or src.dtypes != (profile["dtype"],) or
                not np.array_equal(src.read(1), expected) or
                not np.array_equal(src.read_masks(1) == 0, expected == profile["nodata"])):
            raise ValueError("Categorical raster failed grid, pixel or NoData verification")
    return ctx.record("export", path)["sha256"]


def polygons(ctx, values, included, transform, crs, labels):
    from guizhi.infrastructure.operations.raster_backend import shapes

    rows, geometries = [], []
    for geometry, code in shapes(values, mask=included, transform=transform, connectivity=4):
        ctx.check()
        if len(rows) >= MAX_POLYGONS:
            raise ValueError("Categorical result exceeds the limit of 50,000 boundary polygons")
        polygon = shape(geometry)
        if not polygon.is_valid:
            corrected = make_valid(polygon, method="structure", keep_collapsed=False)
            if (corrected.is_empty or corrected.geom_type not in {"Polygon", "MultiPolygon"} or
                    not isclose(corrected.area, polygon.area, rel_tol=1e-10)):
                raise ValueError("Categorical boundary cannot be represented as a valid polygon")
            polygon = corrected
        rows.append(labels(int(code)))
        geometries.append(polygon)
    columns = list(labels(1))
    frame = gpd.GeoDataFrame(rows, columns=columns, geometry=geometries, crs=crs)
    ctx.publish(frame, geometry_type="Unknown", artifact="boundaries", filename="boundaries.gpkg")


def classify(ctx, model: ModelProfile, observed_on: date, region: str, sensor: str):
    from guizhi.infrastructure.operations.raster_backend import rio

    if len(ctx.rasters) != 1 or len(ctx.models) != 1:
        raise ValueError("Classification requires one GeoTIFF and one ONNX model")
    if region != model.region or sensor != model.sensor or not model.start <= observed_on <= model.end:
        raise ValueError("Observation region, sensor or date is outside the model profile")
    observation = Classification(model=model, observed_on=observed_on)
    info = observation.model_dump(mode="json")
    out = ctx.path("classes.tif")
    with rio.open(ctx.rasters[0]) as src:
        check_grid(src, model)
        if src.descriptions != tuple(model.bands):
            raise ValueError("Band names and order must match the model profile")
        for key, expected in {"sensor": sensor, "region": region, "observed_on": observed_on.isoformat()}.items():
            actual = src.tags(ns="GUIZHI").get(key)
            if actual is not None and actual != expected:
                raise ValueError(f"GeoTIFF observation metadata conflicts with {key}")
        session = session_for(ctx.models[0], model)
        classes = np.empty(src.shape, dtype="uint8")
        profile = profile_for(src, "uint8", CLASS_NODATA)
        with rio.open(out, "w", **profile) as dst:
            dst.update_tags(ns="GUIZHI", classification=observation.model_dump_json())
            for _, window in dst.block_windows(1):
                block = classify_window(ctx, src, window, session, model)
                classes[window.toslices()] = block
                dst.write(block, 1, window=window)
        polygons(ctx, classes, (classes != 0) & (classes != CLASS_NODATA), src.transform, src.crs,
                 lambda code: {"class_id": code, "class_name": model.classes[code - 1]})
    revision = raster_receipt(ctx, out, classes, profile)
    counts = np.bincount(classes.ravel(), minlength=256)
    statistics = {"evaluation": model.evaluation, "known_pixels": int(counts[1:CLASS_NODATA].sum()),
                  "unknown_pixels": int(counts[0]), "nodata_pixels": int(counts[CLASS_NODATA]),
                  "class_pixels": {name: int(counts[i]) for i, name in enumerate(model.classes, 1)}}
    ctx.json("classification.json", {**info, "statistics": statistics}, "classification")
    return {"data_revision": revision, "statistics": statistics}


def classified(src):
    tag = src.tags(ns="GUIZHI").get("classification")
    if not tag or src.count != 1 or src.dtypes != ("uint8",) or src.nodata != CLASS_NODATA:
        raise ValueError("Changes require categorical GeoTIFFs produced by imagery.classify")
    info = Classification.model_validate_json(tag)
    check_grid(src, info.model)
    values = src.read(1)
    if np.any((values > len(info.model.classes)) & (values != CLASS_NODATA)):
        raise ValueError("Classification contains codes outside its model profile")
    return info, values


def change(ctx):
    from guizhi.infrastructure.operations.raster_backend import rio

    require("imagery")
    first, second = ctx.rasters
    out = ctx.path("change.tif")
    with rio.open(first) as start, rio.open(second) as end:
        if (start.crs, start.transform, start.shape) != (end.crs, end.transform, end.shape):
            raise ValueError("Change comparison requires two exactly aligned categorical grids")
        before, old = classified(start)
        after, new = classified(end)
        if before.model != after.model or before.observed_on >= after.observed_on:
            raise ValueError("Compare ascending observation dates under one model profile")
        missing = (old == CLASS_NODATA) | (new == CLASS_NODATA)
        unknown = (~missing) & ((old == 0) | (new == 0))
        comparable = ~(missing | unknown)
        changed = comparable & (old != new)
        values = np.zeros(old.shape, dtype="uint16")
        values[changed] = old[changed].astype("uint16") * 256 + new[changed]
        values[missing], values[unknown] = CHANGE_NODATA, CHANGE_UNKNOWN
        pixel_area = start.res[0] * start.res[1]
        profile = profile_for(start, "uint16", CHANGE_NODATA)
        with rio.open(out, "w", **profile) as dst:
            dst.update_tags(ns="GUIZHI", change="old_class * 256 + new_class; 0 unchanged; 65534 unknown",
                            start=before.observed_on.isoformat(), end=after.observed_on.isoformat(),
                            model_sha256=before.model.sha256, evaluation=before.model.evaluation)
            dst.write(values, 1)
        def transition(code):
            old_code, new_code = divmod(code, 256)
            return {"from_id": old_code, "to_id": new_code}

        polygons(ctx, values, changed, start.transform, start.crs, transition)
    revision = raster_receipt(ctx, out, values, profile)
    names = before.model.classes
    transitions = old[comparable].astype("uint16") * 256 + new[comparable]
    counts = np.bincount(transitions)
    rows = []
    for code in np.flatnonzero(counts):
        a, b = divmod(int(code), 256)
        count = int(counts[code])
        rows.append((names[a - 1], names[b - 1], count, count * pixel_area))
    statistics = {"evaluation": before.model.evaluation, "changed_pixels": int(changed.sum()),
                  "changed_area_m2": float(changed.sum() * pixel_area), "unknown_pixels": int(unknown.sum()),
                  "nodata_pixels": int(missing.sum()), "compared_pixels": int(comparable.sum())}
    note = (f"{before.observed_on} 至 {after.observed_on}。"
            f"未知 {statistics['unknown_pixels']} 像元，缺测 {statistics['nodata_pixels']} 像元。")
    if before.model.evaluation == "simulation":
        note += "模拟样本分析，适用范围为模型清单中的模拟区域。"
    publish_report(ctx, "影像地类变化核查", ("前期地类", "后期地类", "像元数", "面积（平方米）"), rows, note=note)
    ctx.json("change.json", {"start": before.model_dump(mode="json"), "end": after.model_dump(mode="json"),
                            "transitions": rows, "statistics": statistics}, "change")
    return {"data_revision": revision, "statistics": statistics}
