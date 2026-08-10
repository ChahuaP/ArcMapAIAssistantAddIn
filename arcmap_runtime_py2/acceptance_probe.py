# -*- coding: utf-8 -*-
"""Independent ArcPy acceptance probe (Python 2.7).

This module deliberately knows nothing about execution receipts.  It reopens a
sealed staged output through ArcPy and emits a canonical, content-addressed
document for the Gateway acceptance boundary.
"""
from __future__ import absolute_import

import hashlib
import json
import os

import arcpy

try:
    import path_utils
    import context_reader
    import map_state_observation
except ImportError:
    from . import path_utils
    from . import context_reader
    from . import map_state_observation


try:
    unicode
except NameError:
    unicode = str


class AcceptanceProbeError(RuntimeError):
    pass


def probe_unit(source_publish_unit_path):
    """Enumerate every logical dataset in one staged FileGDB exactly once."""
    root = path_utils.abspath(path_utils.to_unicode_path(source_publish_unit_path))
    if not root.lower().endswith(u".gdb") or not path_utils.isdir(root):
        raise AcceptanceProbeError(u"source publish unit must be an existing FileGDB: %s" % root)
    previous_workspace = getattr(arcpy.env, "workspace", None)
    datasets = []
    try:
        arcpy.env.workspace = root
        datasets.extend(_workspace_datasets(root))
    finally:
        arcpy.env.workspace = previous_workspace
    datasets = sorted(set(datasets))
    document = {"source_publish_unit_path": root, "datasets": datasets,
                "members": _directory_members(root)}
    if not document["members"]:
        raise AcceptanceProbeError(u"FileGDB has no physical members: %s" % root)
    document["manifest_digest"] = _digest(document)
    return document


def _workspace_datasets(root):
    """Enumerate every ArcPy-visible FileGDB object, including non-feature data."""
    walk = getattr(getattr(arcpy, "da", None), "Walk", None)
    if walk is None:
        raise AcceptanceProbeError(u"ArcPy da.Walk is required for complete FileGDB inventory")
    found = []
    for directory, names, objects in walk(root, datatype=[
            "FeatureClass", "Table", "RasterDataset", "RelationshipClass",
            "Topology", "NetworkDataset", "GeometricNetwork", "Terrain", "MosaicDataset"]):
        for name in names:
            path = os.path.join(directory, name)
            found.append(path_utils.to_unicode_path(os.path.relpath(path, root)).replace("\\", "/"))
        for name in objects:
            path = os.path.join(directory, name)
            found.append(path_utils.to_unicode_path(os.path.relpath(path, root)).replace("\\", "/"))
    return found


def probe(output_id, kind, staged_path):
    """Read one staged artifact and return its deterministic probe document."""
    _require_text(output_id, "output_id")
    _require_text(kind, "kind")
    _require_text(staged_path, "staged_path")
    canonical_path = path_utils.abspath(path_utils.to_unicode_path(staged_path))
    if not arcpy.Exists(canonical_path):
        raise AcceptanceProbeError(u"staged artifact does not exist: %s" % canonical_path)

    description = arcpy.Describe(canonical_path)
    fields = sorted([path_utils.to_unicode_path(field.name) for field in arcpy.ListFields(canonical_path)])
    geometry = _geometry(description)
    spatial_reference = _spatial_reference(description)
    feature_count = _feature_count(canonical_path, kind)
    members = _members(canonical_path, kind)
    if not members:
        raise AcceptanceProbeError(u"staged artifact has no physical members: %s" % canonical_path)
    document = {
        "output_id": output_id,
        "kind": kind,
        "canonical_path": canonical_path,
        "exists": True,
        "geometry": geometry,
        "spatial_reference": spatial_reference,
        "fields": fields,
        "feature_count": feature_count,
        "members": members,
    }
    document["manifest_digest"] = _digest(document)
    return document


def probe_map_state(output_id, postcondition, arguments):
    """Independently re-read and verify one sealed map-state postcondition.

    Execution receipts are deliberately not an input here.  The only evidence
    is the ArcMap state read at probe time, tied to the exact postcondition and
    arguments the Gateway sent in its fenced command.
    """
    _require_text(output_id, "output_id")
    if not isinstance(postcondition, dict) or not map_state_observation.supports(postcondition.get("kind")):
        raise AcceptanceProbeError(u"unsupported sealed map-state postcondition")
    if not isinstance(arguments, dict):
        raise AcceptanceProbeError(u"map-state probe arguments are required")
    context = context_reader.read_context()
    observation = map_state_observation.observe(
        {"capability_contract": {"postconditions": [postcondition]}},
        postcondition, arguments, {}, context, {}, None)
    check = observation.get("map_state_check") if isinstance(observation, dict) else None
    if not isinstance(check, dict) or check.get("verdict") not in ("passed", "failed"):
        raise AcceptanceProbeError(u"map-state observation did not return a verdict")
    document = {
        "probe_type": "map_state",
        "output_id": output_id,
        "kind": "map_state",
        "postcondition": postcondition,
        "arguments": arguments,
        "map_state": _map_state_snapshot(context),
        "map_state_check": check,
        "passed": check["verdict"] == "passed",
    }
    document["manifest_digest"] = _digest(document)
    return document


def _map_state_snapshot(context):
    """Stable live-state digest input; excludes receipt-derived values."""
    layers = context.get("layers") if isinstance(context, dict) else []
    return {
        "active_view": context.get("active_view"),
        "extent": context.get("extent"),
        "layers": [{
            "layer_ref": item.get("layer_ref"), "name": item.get("name"),
            "long_name": item.get("long_name"), "visible": bool(item.get("visible")),
            "selected_count": int(item.get("selected_count", 0) or 0),
        } for item in layers if isinstance(item, dict)],
    }


def _require_text(value, name):
    if not isinstance(value, unicode) or not value:
        raise AcceptanceProbeError(u"%s is required" % name)


def _geometry(description):
    value = getattr(description, "shapeType", None) or getattr(description, "shapeType", u"")
    return path_utils.to_unicode_path(value) if value else u"not_applicable"


def _spatial_reference(description):
    reference = getattr(description, "spatialReference", None)
    name = getattr(reference, "name", None) if reference is not None else None
    return path_utils.to_unicode_path(name) if name else u""


def _feature_count(path, kind):
    if kind not in ("feature_class", "raster"):
        return 0
    try:
        return int(arcpy.GetCount_management(path).getOutput(0))
    except (AttributeError, TypeError, ValueError, arcpy.ExecuteError):
        raise AcceptanceProbeError(u"cannot count staged artifact: %s" % path)


def _members(canonical_path, kind):
    gdb_root = _gdb_root(canonical_path)
    if gdb_root:
        return _directory_members(gdb_root)
    if not path_utils.isfile(canonical_path):
        raise AcceptanceProbeError(u"staged file is missing: %s" % canonical_path)
    return [_member(canonical_path, os.path.basename(canonical_path))]


def _gdb_root(path):
    current = path
    while current:
        if current.lower().endswith(".gdb") and path_utils.isdir(current):
            return current
        parent = path_utils.dirname(current)
        if parent == current:
            return None
        current = parent
    return None


def _directory_members(root):
    result = []
    for directory, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            path = os.path.join(directory, name)
            if name.lower().endswith(".lock"):
                continue
            relative = os.path.relpath(path, root).replace("\\", "/")
            result.append(_member(path, relative))
    return result


def _member(path, relative_path):
    return {
        "relative_path": path_utils.to_unicode_path(relative_path).replace("\\", "/"),
        "size": int(path_utils.getsize(path)),
        "sha256": _sha256(path),
    }


def _sha256(path):
    digest = hashlib.sha256()
    with path_utils.open_binary(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _digest(document):
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if not isinstance(encoded, bytes):
        encoded = encoded.encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
