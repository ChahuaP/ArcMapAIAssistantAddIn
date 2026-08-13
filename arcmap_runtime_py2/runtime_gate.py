# -*- coding: utf-8 -*-
"""ArcMap-side reset/load contract for the formal runtime gate."""
from __future__ import absolute_import

import os
import json
import tempfile
import hashlib

import arcpy
from shared_runtime.runtime_gate_protocol import (
    RUNTIME_GATE_PROTOCOL, SCHEMA_SHA256, validate_request, validate_result,
)

try:
    import context_reader
    import arcmap_desktop_selection
    import acceptance_probe
    import deployment_identity
except ImportError:
    from . import context_reader
    from . import arcmap_desktop_selection
    from . import acceptance_probe
    from . import deployment_identity


def apply(request):
    validate_request(request)
    if request.get("deployment_hash") != deployment_identity.deployment_hash():
        raise RuntimeError("runtime-gate deployment identity mismatch")
    mode = request.get("mode")
    if mode == "prepare":
        _prepare(request)
    elif mode == "restore":
        _restore(request)
    elif mode == "evaluate":
        _evaluate(request)
    else:
        raise RuntimeError("runtime-gate mode must be prepare, restore, or evaluate")
    _write_result(request, _lifecycle_context_hash())


def _lifecycle_context_hash():
    """Hash user input state while excluding the server-owned output GDB."""
    context = context_reader.read_context()
    context["default_gdb"] = ""
    context.pop("content_hash", None)
    return context_reader.context_hash(context)


def _write_result(request, context_hash):
    lifecycle_id = request.get("lifecycle_lease_id")
    path = request.get("result_path")
    if not isinstance(lifecycle_id, basestring) or not lifecycle_id or not isinstance(path, basestring) or not path:
        raise RuntimeError("runtime-gate lacks a lifecycle result fence")
    directory = os.path.dirname(path)
    if not directory or not os.path.isdir(directory):
        raise RuntimeError("runtime-gate result directory is unavailable")
    payload = {"protocol": RUNTIME_GATE_PROTOCOL, "schema_hash": SCHEMA_SHA256,
               "lifecycle_lease_id": lifecycle_id, "context_hash": context_hash}
    if request.get("mode") == "prepare":
        payload["staging_gdb"] = os.path.abspath(request["staging_gdb"])
    if request.get("mode") == "evaluate":
        payload["truth_ids"] = _truth_ids(request)
        payload["artifact_manifests"] = _artifact_manifests(request)
        encoded = json.dumps({"artifacts": request["artifacts"], "truth_ids": payload["truth_ids"],
                              "artifact_manifests": payload["artifact_manifests"]},
                             sort_keys=True, separators=(",", ":")).encode("utf-8")
        payload["evidence_hash"] = hashlib.sha256(encoded).hexdigest()
    validate_result(payload, request.get("mode"))
    descriptor, temporary = tempfile.mkstemp(prefix=".runtime-gate-", suffix=".tmp", dir=directory)
    try:
        handle = os.fdopen(descriptor, "wb")
        try:
            handle.write(json.dumps(payload, sort_keys=True).encode("utf-8"))
            handle.flush(); os.fsync(handle.fileno())
        finally:
            handle.close()
        if os.path.exists(path):
            os.remove(path)
        os.rename(temporary, path)
    except Exception:
        if os.path.exists(temporary): os.remove(temporary)
        raise


def _evaluate(request):
    expected = request.get("expected")
    artifacts = request.get("artifacts")
    if not isinstance(expected, dict) or not isinstance(artifacts, dict):
        raise RuntimeError("runtime-gate evaluator lacks expected identities")
    for output_id, requirement in expected.items():
        artifact = artifacts.get(output_id)
        if not isinstance(requirement, dict) or not isinstance(artifact, dict):
            raise RuntimeError("runtime-gate evaluator artifact binding is missing")
        _verify_published_manifest(artifact)
        path, field = artifact.get("destination_dataset_path"), requirement.get("field")
        if not isinstance(path, basestring) or not arcpy.Exists(path) or not isinstance(field, basestring):
            raise RuntimeError("runtime-gate evaluator cannot read the declared artifact")
        if field not in [item.name for item in arcpy.ListFields(path)]:
            raise RuntimeError("runtime-gate evaluator declared ID field is absent")


def _truth_ids(request):
    result = {}
    for output_id, requirement in request["expected"].items():
        path = request["artifacts"][output_id]["destination_dataset_path"]
        result[output_id] = sorted([unicode(row[0]) for row in arcpy.da.SearchCursor(path, [requirement["field"]])])
    return result


def _artifact_manifests(request):
    result = {}
    for output_id, artifact in request["artifacts"].items():
        result[output_id] = acceptance_probe.probe(
            output_id, artifact["kind"], artifact["destination_dataset_path"],
            artifact["output_format"])
    return result


def _prepare(request):
    sources = request.get("source_layers")
    staging_gdb = request.get("staging_gdb")
    if not isinstance(sources, list) or not sources or not isinstance(staging_gdb, basestring) or not staging_gdb:
        raise RuntimeError("runtime-gate prepare lacks source_layers or staging_gdb")
    if any(not isinstance(path, basestring) or not path or not arcpy.Exists(path) for path in sources):
        raise RuntimeError("runtime-gate source layer does not exist")
    staging_gdb = os.path.abspath(staging_gdb)
    if not staging_gdb.lower().endswith(".gdb"):
        raise RuntimeError("runtime-gate staging_gdb must be an arm-private FileGDB")
    parent = os.path.dirname(staging_gdb)
    if not os.path.isdir(parent):
        os.makedirs(parent)
    if not arcpy.Exists(staging_gdb):
        arcpy.CreateFileGDB_management(parent, os.path.basename(staging_gdb))
    if not arcpy.Exists(staging_gdb):
        raise RuntimeError("runtime-gate failed to create the arm-private FileGDB")
    arcpy.env.workspace = staging_gdb
    arcpy.env.scratchWorkspace = staging_gdb
    mxd = arcpy.mapping.MapDocument("CURRENT")
    mxd.defaultGeodatabase = staging_gdb
    data_frame = _active_frame(mxd)
    _clear_layers(mxd, data_frame)
    for path in sources:
        arcpy.mapping.AddLayer(data_frame, arcpy.mapping.Layer(path), "BOTTOM")
    _clear_selection(mxd, data_frame)
    context = context_reader.read_context()
    if len(context.get("layers", [])) != len(sources):
        raise RuntimeError("runtime-gate loaded layer count differs from declared sources")


def _restore(request):
    artifacts = request.get("artifacts")
    if not isinstance(artifacts, dict):
        raise RuntimeError("runtime-gate restore artifacts must be an object")
    before = _lifecycle_context_hash()
    if before != request.get("initial_digest"):
        raise RuntimeError("runtime-gate restore initial context hash mismatch")
    mxd = arcpy.mapping.MapDocument("CURRENT")
    data_frame = _active_frame(mxd)
    _clear_selection(mxd, data_frame)
    existing = set(item.get("data_source") for item in context_reader.read_context().get("layers", []))
    for name in sorted(artifacts):
        artifact = artifacts[name]
        if not isinstance(artifact, dict):
            raise RuntimeError("runtime-gate artifact is malformed")
        _verify_published_manifest(artifact)
        path = artifact.get("destination_dataset_path")
        if not isinstance(path, basestring) or not path or not arcpy.Exists(path):
            raise RuntimeError("runtime-gate artifact path does not exist")
        if path not in existing:
            layer = arcpy.mapping.Layer(path)
            layer.name = name
            arcpy.mapping.AddLayer(data_frame, layer, "TOP")
            existing.add(path)


def _verify_published_manifest(artifact):
    required = set(("output_id", "kind", "output_format", "destination_dataset_path",
                    "destination_publish_unit_path", "publication_kind", "members",
                    "semantic_evidence", "acceptance_evidence_hash", "evidence_hash"))
    if not isinstance(artifact, dict) or set(artifact) != required:
        raise RuntimeError("runtime-gate requires a complete published artifact manifest")
    evidence = dict(artifact)
    sealed = evidence.pop("evidence_hash")
    encoded = json.dumps(evidence, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    if hashlib.sha256(encoded).hexdigest() != sealed:
        raise RuntimeError("runtime-gate published artifact evidence hash mismatch")
    unit = artifact["destination_publish_unit_path"]
    expected = artifact["members"]
    actual = acceptance_probe.file_manifest(unit)
    if actual != expected:
        raise RuntimeError("runtime-gate published artifact members changed")


def _active_frame(mxd):
    frame = getattr(mxd, "activeDataFrame", None)
    if frame is None:
        raise RuntimeError("ArcMap has no active data frame")
    return frame


def _clear_layers(mxd, data_frame):
    layers = list(arcpy.mapping.ListLayers(mxd, "", data_frame))
    for layer in layers:
        arcpy.mapping.RemoveLayer(data_frame, layer)


def _clear_selection(mxd, data_frame):
    for layer in arcpy.mapping.ListLayers(mxd, "", data_frame):
        if getattr(layer, "isFeatureLayer", False):
            arcmap_desktop_selection.restore_oids(layer, [])
