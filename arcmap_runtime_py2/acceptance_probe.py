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
from shared_runtime.file_semantics import inspect_file, FileSemanticError

try:
    import semantic_acceptance
except ImportError:
    from . import semantic_acceptance

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


def file_manifest(publish_unit_path):
    root = path_utils.abspath(path_utils.to_unicode_path(publish_unit_path))
    if path_utils.isdir(root):
        return _directory_members(root)
    if path_utils.isfile(root):
        return [_member(root, path_utils.basename(root))]
    raise AcceptanceProbeError(u"publish unit does not exist: %s" % root)


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


def probe(output_id, kind, staged_path, output_format=None):
    """Read one staged artifact and return its deterministic probe document."""
    _require_text(output_id, "output_id")
    _require_text(kind, "kind")
    _require_text(staged_path, "staged_path")
    canonical_path = path_utils.abspath(path_utils.to_unicode_path(staged_path))
    if kind == "file":
        if not path_utils.isfile(canonical_path):
            raise AcceptanceProbeError(u"staged file does not exist: %s" % canonical_path)
        try:
            semantics = inspect_file(canonical_path, output_format)
        except FileSemanticError as exc:
            raise AcceptanceProbeError(unicode(exc))
        document = {
            "output_id": output_id, "kind": kind, "canonical_path": canonical_path,
            "exists": True, "geometry": u"not_applicable", "spatial_reference": u"",
            "fields": [], "feature_count": 0,
            "members": [_member(canonical_path, os.path.basename(canonical_path))],
            "file_semantics": semantics,
        }
        document["manifest_digest"] = _digest(document)
        return document
    if not arcpy.Exists(canonical_path):
        raise AcceptanceProbeError(u"staged artifact does not exist: %s" % canonical_path)

    description = arcpy.Describe(canonical_path)
    fields = _field_specs(canonical_path)
    geometry = _geometry_evidence(canonical_path, description, kind)
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
        "geometry": geometry["geometry_type"],
        "geometry_evidence": geometry,
        "spatial_reference": spatial_reference,
        "fields": fields,
        "feature_count": feature_count,
        "extent": _extent(description),
        "record_content": _record_content(canonical_path, description, fields, kind),
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


def probe_contract(acceptance_contract, document):
    """Return per-rule independent proof status.

    This boundary never reuses an execution receipt.  Rules needing source
    datasets or map state that were not sealed into this probe request remain
    ``Unresolved``; the Gateway rejects them rather than manufacturing proof.
    """
    if not isinstance(acceptance_contract, dict):
        return [{"proof_id": "acceptance:contract", "status": "Unresolved",
                 "reason": "sealed acceptance contract is missing"}]
    rules = acceptance_contract.get("rules")
    if not isinstance(rules, list):
        return [{"proof_id": "acceptance:contract", "status": "Unresolved",
                 "reason": "sealed acceptance rules are missing"}]
    result = []
    for rule in rules:
        proof_id = rule.get("proof_id") if isinstance(rule, dict) else None
        predicate = rule.get("predicate") if isinstance(rule, dict) else None
        if not isinstance(proof_id, unicode) or not isinstance(predicate, dict):
            result.append({"proof_id": proof_id or u"acceptance:malformed", "status": "Violated",
                           "reason": "malformed sealed acceptance rule"})
            continue
        expected_output = rule.get("output_id")
        if expected_output is not None and expected_output != document.get("output_id"):
            continue
        result.append(semantic_acceptance.evaluate(rule, document))
    return result


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


def _geometry_evidence(path, description, kind):
    """Measure geometry independently; a non-empty shapeType is not validity.

    ArcPy environments that cannot provide a required primitive deliberately
    return ``Unresolved``.  Acceptance must reject that status instead of
    silently treating unavailable validation as a successful geometry check.
    """
    value = getattr(description, "shapeType", None) or u""
    geometry_type = path_utils.to_unicode_path(value) if value else u"not_applicable"
    if geometry_type == u"not_applicable":
        return {"geometry_type": geometry_type, "status": "Proven",
                "null_count": 0, "empty_count": 0, "invalid_count": 0,
                "method": "not_applicable"}
    evidence = {"geometry_type": geometry_type, "status": "Unresolved",
                "null_count": None, "empty_count": None, "invalid_count": None,
                "method": "arcpy.da.SearchCursor+CheckGeometry"}
    search_cursor = getattr(getattr(arcpy, "da", None), "SearchCursor", None)
    if search_cursor is None:
        evidence["reason"] = "arcpy.da.SearchCursor is unavailable"
        return evidence
    try:
        null_count, empty_count = 0, 0
        with search_cursor(path, ["SHAPE@"]) as rows:
            for row in rows:
                shape = row[0]
                if shape is None:
                    null_count += 1
                elif bool(getattr(shape, "isEmpty", False)):
                    empty_count += 1
        invalid = _invalid_geometry_count(path)
    except Exception as exc:
        evidence["reason"] = path_utils.to_unicode_path(unicode(exc))
        return evidence
    evidence.update({"status": "Proven", "null_count": null_count,
                     "empty_count": empty_count, "invalid_count": invalid})
    return evidence


def _invalid_geometry_count(path):
    check = getattr(arcpy, "CheckGeometry_management", None)
    delete = getattr(arcpy, "Delete_management", None)
    if check is None or delete is None:
        raise AcceptanceProbeError(u"ArcPy CheckGeometry_management is required for geometry acceptance")
    scratch = os.path.join(getattr(arcpy.env, "scratchGDB", None) or os.path.dirname(path),
                           "geopilot_check_%s" % hashlib.sha256(path.encode("utf-8")).hexdigest()[:16])
    try:
        result = check(path, scratch)
        table = result.getOutput(0) if hasattr(result, "getOutput") else scratch
        return int(arcpy.GetCount_management(table).getOutput(0))
    finally:
        try:
            if arcpy.Exists(scratch):
                delete(scratch)
        except Exception:
            pass


def _field_specs(path):
    result = []
    for field in arcpy.ListFields(path):
        domain = getattr(field, "domain", None)
        result.append({
            "name": path_utils.to_unicode_path(field.name),
            "type": path_utils.to_unicode_path(getattr(field, "type", u"") or u""),
            "nullable": bool(getattr(field, "isNullable", False)),
            "precision": _integer_or_none(getattr(field, "precision", None)),
            "scale": _integer_or_none(getattr(field, "scale", None)),
            "length": _integer_or_none(getattr(field, "length", None)),
            "domain": path_utils.to_unicode_path(domain) if domain else None,
        })
    return sorted(result, key=lambda item: item["name"])


def _integer_or_none(value):
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extent(description):
    value = getattr(description, "extent", None)
    if value is None:
        return None
    try:
        return {"xmin": float(value.XMin), "ymin": float(value.YMin),
                "xmax": float(value.XMax), "ymax": float(value.YMax)}
    except (AttributeError, TypeError, ValueError):
        return None


def _record_content(path, description, fields, kind):
    """Content-addressed stable-ID and attribute evidence, never a receipt."""
    if kind not in ("feature_class", "table"):
        return {"status": "Proven", "stable_id_field": None, "record_hash": None,
                "attribute_hashes": {}}
    cursor = getattr(getattr(arcpy, "da", None), "SearchCursor", None)
    if cursor is None:
        return {"status": "Unresolved", "reason": "arcpy.da.SearchCursor is unavailable"}
    oid = getattr(description, "OIDFieldName", None) or None
    names = [item["name"] for item in fields if item["type"] not in ("Geometry", "Raster", "Blob")]
    if oid and oid not in names:
        names.insert(0, oid)
    if not names:
        return {"status": "Unresolved", "reason": "no independently readable attributes"}
    row_digest, columns = hashlib.sha256(), dict((name, hashlib.sha256()) for name in names)
    try:
        with cursor(path, names) as rows:
            for row in rows:
                canonical = [u"" if value is None else path_utils.to_unicode_path(unicode(value)) for value in row]
                encoded = json.dumps(canonical, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                row_digest.update(encoded + b"\n")
                for index, name in enumerate(names):
                    columns[name].update(canonical[index].encode("utf-8") + b"\n")
    except Exception as exc:
        return {"status": "Unresolved", "reason": path_utils.to_unicode_path(unicode(exc))}
    return {"status": "Proven", "stable_id_field": path_utils.to_unicode_path(oid) if oid else None,
            "record_hash": row_digest.hexdigest(),
            "attribute_hashes": dict((name, digest.hexdigest()) for name, digest in columns.items())}


def _spatial_reference(description):
    reference = getattr(description, "spatialReference", None)
    name = getattr(reference, "name", None) if reference is not None else None
    if not name:
        return u""
    return {"name": path_utils.to_unicode_path(name),
            "factory_code": _integer_or_none(getattr(reference, "factoryCode", None)),
            "type": path_utils.to_unicode_path(getattr(reference, "type", u"") or u"")}


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
