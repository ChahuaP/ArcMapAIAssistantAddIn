# -*- coding: utf-8 -*-
from __future__ import absolute_import

import hashlib

import arcpy
from shared_runtime import context_fingerprint

try:
    import path_utils
except ImportError:
    from . import path_utils

try:
    import arcmap_desktop_selection
except ImportError:
    from . import arcmap_desktop_selection


try:
    unicode
except NameError:
    unicode = str

try:
    INTEGER_TYPES = (int, long)
except NameError:
    INTEGER_TYPES = (int,)


MAX_VALUE_TEXT_LENGTH = 120
ARCPY_EXECUTE_ERROR = getattr(arcpy, "ExecuteError", RuntimeError)


def read_context():
    mxd = arcpy.mapping.MapDocument("CURRENT")
    data_frames = arcpy.mapping.ListDataFrames(mxd)
    data_frame = getattr(mxd, "activeDataFrame", None)
    if data_frames and data_frame is None:
        raise RuntimeError("ArcMap has data frames but no active data frame.")
    if data_frame is not None and data_frame not in data_frames:
        raise RuntimeError("ArcMap active data frame is not in this document.")
    layers = []
    if data_frame is not None:
        for index, layer in enumerate(arcpy.mapping.ListLayers(mxd, "", data_frame)):
            layers.append(_layer_info(layer, index))

    context = {
        "mxd_path": _mxd_path(mxd),
        "is_saved": bool(_mxd_path(mxd)),
        "default_gdb": _default_geodatabase(mxd),
        "active_view": getattr(mxd, "activeView", None),
        "data_frame": data_frame.name if data_frame is not None else None,
        "spatial_reference": _spatial_reference(data_frame),
        "extent": _extent(data_frame),
        "layers": layers
    }
    context["edit_session_state"] = _edit_session_state(layers)
    context["content_hash"] = context_hash(context)
    return context


def context_hash(context):
    return context_fingerprint.context_hash(context)


def sample_values(layer_ref, field_names, max_rows, max_samples):
    if not isinstance(layer_ref, unicode) or not layer_ref.startswith(u"layer:"):
        raise ValueError("sample layer_ref is invalid")
    index = int(layer_ref.split(u":", 1)[1])
    if index < 0 or not isinstance(field_names, list) or not field_names or max_rows <= 0 or max_samples <= 0:
        raise ValueError("sample request is invalid")
    mxd = arcpy.mapping.MapDocument("CURRENT")
    frames = arcpy.mapping.ListDataFrames(mxd)
    if not frames:
        raise RuntimeError("ArcMap has no active data frame.")
    data_frame = getattr(mxd, "activeDataFrame", None)
    if data_frame is None:
        raise RuntimeError("ArcMap has data frames but no active data frame.")
    if data_frame not in frames:
        raise RuntimeError("ArcMap active data frame is not in this document.")
    layers = arcpy.mapping.ListLayers(mxd, "", data_frame)
    if index >= len(layers):
        raise RuntimeError("sample layer_ref does not exist.")
    layer = layers[index]
    available = set(field.name for field in arcpy.ListFields(layer))
    if any(name not in available for name in field_names):
        raise RuntimeError("sample field does not exist.")
    values = dict((name, []) for name in field_names)
    seen = dict((name, set()) for name in field_names)
    with arcpy.da.SearchCursor(layer, field_names) as cursor:
        for row_number, row in enumerate(cursor):
            if row_number >= max_rows:
                break
            for number, raw in enumerate(row):
                if raw is None:
                    continue
                name = field_names[number]
                text = _sample_text(raw)
                if text and text not in seen[name] and len(values[name]) < max_samples:
                    seen[name].add(text)
                    values[name].append(text)
    return values


def _mxd_path(mxd):
    path = getattr(mxd, "filePath", None)
    if path and path_utils.exists(path):
        return path_utils.to_unicode_path(path)
    return ""


def _default_geodatabase(mxd):
    default_gdb = getattr(mxd, "defaultGeodatabase", None)
    if default_gdb:
        return path_utils.to_unicode_path(default_gdb)
    env = getattr(arcpy, "env", None)
    workspace = getattr(env, "workspace", None)
    if workspace and unicode(workspace).lower().endswith(u".gdb"):
        return path_utils.to_unicode_path(workspace)
    return ""


def _spatial_reference(data_frame):
    if data_frame is None:
        return None
    sr = getattr(data_frame, "spatialReference", None)
    if sr is None:
        return None
    return {"name": getattr(sr, "name", ""), "factoryCode": getattr(sr, "factoryCode", None)}


def _extent(data_frame):
    if data_frame is None:
        return None
    extent = getattr(data_frame, "extent", None)
    if extent is None:
        return None
    return {
        "XMin": context_fingerprint.canonical_coordinate(extent.XMin),
        "YMin": context_fingerprint.canonical_coordinate(extent.YMin),
        "XMax": context_fingerprint.canonical_coordinate(extent.XMax),
        "YMax": context_fingerprint.canonical_coordinate(extent.YMax)
    }


def _edit_session_state(layers):
    workspaces = set()
    for layer in layers:
        source = layer.get("data_source")
        if source:
            workspaces.add(path_utils.dirname(source))
    if not workspaces:
        return "none"
    states = []
    for workspace in sorted(workspaces):
        try:
            states.append(bool(arcpy.da.Editor(workspace).isEditing))
        except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError):
            return "unknown"
    if all(not state for state in states):
        return "none"
    if all(states):
        return "single"
    return "mixed"


def _layer_info(layer, index):
    info = {
        "layer_ref": "layer:%s" % index,
        "name": layer.name,
        "long_name": getattr(layer, "longName", layer.name),
        "visible": bool(getattr(layer, "visible", False)),
        "is_feature_layer": bool(getattr(layer, "isFeatureLayer", False)),
        "data_source": _safe_support(layer, "DATASOURCE", "dataSource"),
        "layer_type": _layer_type(layer),
        "fields": [],
        "selected_count": 0,
        "geometry_type": None,
        "spatial_reference": None
    }
    if info["is_feature_layer"]:
        desc = arcpy.Describe(layer)
        info["geometry_type"] = getattr(desc, "shapeType", None)
        info["spatial_reference"] = _layer_spatial_reference(desc)
        _seal_spatial_unit(info, desc)
        selection_oids = arcmap_desktop_selection.capture_oids(layer, desc)
        info["selected_count"] = len(selection_oids)
        info["selection_hash"] = context_fingerprint.selection_hash(
            u";".join(unicode(oid) for oid in selection_oids))
        try:
            fields = arcpy.ListFields(layer)
            for field in fields:
                info["fields"].append(_field_spec(field))
            info["identity_fields"] = [field.name for field in fields
                                       if unicode(field.type).lower() in (u"oid", u"globalid", u"guid")]
            info["source_content_digest"] = layer_content_digest(layer)
            info["feature_manifest_digest"] = feature_manifest_digest(layer, info["identity_fields"])
        except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError) as exc:
            _layer_warning(info, u"field_read_failed: %s" % _sample_text(exc))
    elif bool(getattr(layer, "isRasterLayer", False)):
        try:
            info["geometry_type"] = u"raster"
            info["raster_content_digest"] = raster_content_digest(layer)
            info["source_content_digest"] = info["raster_content_digest"]
        except (ARCPY_EXECUTE_ERROR, RuntimeError, AttributeError, TypeError) as exc:
            _layer_warning(info, u"raster_read_failed: %s" % _sample_text(exc))
    return info


def _field_spec(field):
    """Seal the complete canonical ABI field semantics from one ArcPy field."""
    domain = getattr(field, "domain", None)
    return {
        "name": _sample_text(field.name),
        "type": _sample_text(getattr(field, "type", u"") or u""),
        "nullable": bool(getattr(field, "isNullable", False)),
        "length": _int_or_none(getattr(field, "length", None)),
        "precision": _int_or_none(getattr(field, "precision", None)),
        "scale": _int_or_none(getattr(field, "scale", None)),
        "domain": _sample_text(domain) if domain else None,
    }


def _int_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def layer_content_digest(layer):
    """Read-only deterministic attribute evidence; never an execution receipt.

    Geometry is excluded here so attribute identity and geometric identity are
    independently provable (see ``feature_manifest_digest`` / ``raster_content_digest``).
    """
    desc = arcpy.Describe(layer)
    fields = [field.name for field in arcpy.ListFields(layer)
              if unicode(field.type).lower() not in (u"blob", u"raster", u"geometry")]
    digest = hashlib.sha256()
    digest.update(unicode(getattr(desc, "catalogPath", layer)).encode("utf-8", "replace"))
    digest.update(u"\x1f".join(fields).encode("utf-8", "replace"))
    with arcpy.da.SearchCursor(layer, fields) as rows:
        for row in rows:
            digest.update(repr(tuple(row)).encode("utf-8", "replace"))
    return digest.hexdigest()


def feature_manifest_digest(layer, identity_fields):
    """Order-independent feature manifest keyed by sealed business identity.

    Every feature is read as (identity tuple, complete non-binary attribute
    tuple, canonical geometry hash) and the whole set is hashed in sorted
    order.  The digest is therefore replayable and order-independent: a single
    changed geometry or attribute value changes one token and the digest, so
    ``source_preserved`` can Violate a silent reshape deterministically.

    Requires a real stable identity on the dataset.  No identity (or unreadable
    cursor) returns None — the caller must then refuse to claim preservation
    rather than fall back to OID ordering.
    """
    if not identity_fields:
        return None
    desc = arcpy.Describe(layer)
    cursor = getattr(getattr(arcpy, "da", None), "SearchCursor", None)
    if cursor is None:
        return None
    all_fields = [field.name for field in arcpy.ListFields(layer)
                  if unicode(field.type).lower() not in (u"blob", u"raster", u"geometry")]
    identity = [name for name in identity_fields if name in all_fields] or list(identity_fields)
    attribute_fields = [name for name in all_fields if name not in identity]
    cursor_fields = list(identity) + attribute_fields + [u"SHAPE@WKB"]
    tokens = []
    try:
        with cursor(layer, cursor_fields) as rows:
            for row in rows:
                values = list(row[:-1])
                wkb = row[-1]
                ident = tuple(unicode(v) if v is not None else u"<null>" for v in values[:len(identity)])
                attrs = tuple(unicode(v) if v is not None else u"<null>" for v in values[len(identity):])
                geom_hash = hashlib.sha256(wkb if isinstance(wkb, str) else bytes(wkb)).hexdigest() if wkb else u"<null>"
                tokens.append(u"\x1f".join(ident) + u"\x1e" + u"\x1f".join(attrs) + u"\x1e" + geom_hash)
    except Exception:
        return None
    if not tokens:
        return None
    digest = hashlib.sha256()
    digest.update(unicode(getattr(desc, "shapeType", u"")).encode("utf-8", "replace"))
    for token in sorted(tokens):
        digest.update(token.encode("utf-8", "replace"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def raster_content_digest(layer):
    """Stable, chunked cell-content digest for raster sources.

    Reads the actual cell values (per band) in memory-bounded row blocks plus
    the NoData mask, grid geometry and CRS, and hashes them.  A raster whose
    metadata is identical but whose cells were tampered with produces a
    different digest.  Directory timestamps are never used.

    If the runtime cannot reliably read cells (no ``RasterToNumPyArray`` /
    numpy, or any read failure), this returns None so the caller refuses to
    claim raster preservation rather than asserting support it cannot prove.
    """
    desc = arcpy.Describe(layer)
    to_numpy = getattr(arcpy, "RasterToNumPyArray", None)
    if to_numpy is None:
        return None
    try:
        import numpy as _np
    except ImportError:
        return None
    raster = arcpy.Raster(layer)
    width = int(getattr(raster, "width", 0) or 0)
    height = int(getattr(raster, "height", 0) or 0)
    if width <= 0 or height <= 0:
        return None
    # Multi-band rasters are not reliably summarizable on this runtime; refuse
    # to claim preservation (return None) so the seal rejects before execution
    # rather than producing an Unresolved at runtime.
    band_count = int(getattr(raster, "bandCount", 1) or 1)
    if band_count > 1:
        return None
    pixel_type = unicode(getattr(raster, "pixelType", u"") or u"")
    if pixel_type.upper() == u"F32" or pixel_type.upper() == u"F64":
        # Floating rasters hash deterministically only when NoData is stable;
        # treat as unsupported to avoid false Proven on this runtime.
        return None
    nodata = getattr(raster, "noDataValue", None)
    digest = hashlib.sha256()
    digest.update(unicode(getattr(desc, "catalogPath", layer)).encode("utf-8", "replace"))
    digest.update(b"\x1f")
    extent = getattr(desc, "extent", None)
    if extent is not None:
        for attr in (u"XMin", u"YMin", u"XMax", u"YMax"):
            digest.update(unicode(context_fingerprint.canonical_coordinate(getattr(extent, attr, None))).encode("utf-8", "replace"))
            digest.update(b"\x1e")
    reference = getattr(desc, "spatialReference", None)
    digest.update(unicode(getattr(reference, "factoryCode", None)).encode("utf-8", "replace"))
    digest.update(b"\x1f")
    digest.update(unicode(getattr(reference, "name", None)).encode("utf-8", "replace"))
    digest.update(b"\x1f")
    digest.update(unicode(getattr(desc, "meanCellWidth", None)).encode("utf-8", "replace"))
    digest.update(b"\x1e")
    digest.update(unicode(getattr(desc, "meanCellHeight", None)).encode("utf-8", "replace"))
    digest.update(b"\x1e")
    digest.update(unicode(nodata).encode("utf-8", "replace"))
    digest.update(b"\x1e")
    # Memory-bounded, windowed read: RasterToNumPyArray accepts a lower-left
    # corner and a column/row count, so the raster is hashed one row-block at a
    # time instead of loading the whole grid.
    block = 256
    extent = getattr(raster, "extent", None)
    cellh = getattr(raster, "meanCellHeight", None) or getattr(desc, "meanCellHeight", None)
    nodata_value = nodata if nodata is not None else 0
    try:
        for top in range(0, height, block):
            row_count = min(block, height - top)
            if extent is not None and cellh:
                lower_left = arcpy.Point(extent.XMin, extent.YMax - (top + row_count) * cellh)
                block_array = to_numpy(raster, lower_left, width, row_count, nodata_value)
            else:
                full = to_numpy(raster, nodata_to_value=nodata_value)
                block_array = full[top:top + row_count, 0:width]
                del full
            if hasattr(block_array, "tobytes"):
                digest.update(bytes(block_array.tobytes()))
            else:
                digest.update(bytes(buffer(block_array)))
            del block_array
    except Exception:
        return None
    return digest.hexdigest()


def _layer_type(layer):
    if bool(getattr(layer, "isFeatureLayer", False)):
        return "FeatureLayer"
    if bool(getattr(layer, "isRasterLayer", False)):
        return "RasterLayer"
    if bool(getattr(layer, "isGroupLayer", False)):
        return "GroupLayer"
    return "Layer"


def _layer_spatial_reference(description):
    spatial_reference = getattr(description, "spatialReference", None)
    name = getattr(spatial_reference, "name", None) if spatial_reference is not None else None
    factory_code = getattr(spatial_reference, "factoryCode", None) if spatial_reference is not None else None
    if isinstance(factory_code, INTEGER_TYPES) and factory_code > 0:
        return "EPSG:%d" % factory_code
    return name if name else None


def _seal_spatial_unit(info, description):
    """Seal the runtime-verified CRS type and meters-per-linear-unit.

    ArcPy Describe is the authority; the acceptance strategy never infers the
    unit from a CRS name.  For geographic CRS the linear unit is angular, so
    meters_per_unit is left unset (degrees are handled by the strategy).
    """
    sr = getattr(description, "spatialReference", None)
    if sr is None:
        return
    crs_type = getattr(sr, "type", None)
    if isinstance(crs_type, (str, unicode)):
        info["crs_type"] = crs_type
    if crs_type and "projected" in unicode(crs_type).lower():
        meters_per_unit = getattr(sr, "metersPerUnit", None)
        if isinstance(meters_per_unit, (int, float)) and not isinstance(meters_per_unit, bool) and meters_per_unit > 0:
            info["meters_per_unit"] = float(meters_per_unit)


def _sample_text(value):
    try:
        text = value if isinstance(value, unicode) else unicode(value)
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError):
        if hasattr(value, "decode"):
            try:
                text = value.decode("utf-8", "ignore")
            except (UnicodeDecodeError, UnicodeEncodeError, TypeError, AttributeError):
                text = u""
        else:
            text = u""
    text = text.strip()
    if len(text) > MAX_VALUE_TEXT_LENGTH:
        text = text[:MAX_VALUE_TEXT_LENGTH]
    return text


def _safe_support(layer, support_name, attr_name):
    try:
        if layer.supports(support_name):
            value = getattr(layer, attr_name)
            if attr_name == "dataSource" and value:
                return path_utils.to_unicode_path(value)
            return value
    except (RuntimeError, AttributeError, TypeError):
        return None
    return None


def _layer_warning(info, message):
    warnings = info.setdefault("warnings", [])
    warnings.append(message)
