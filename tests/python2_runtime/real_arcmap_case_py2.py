# -*- coding: utf-8 -*-
"""Real ArcGIS 10.2 case study for the independent acceptance layer.

Unlike the headless interval-geometry simulation, this script runs on real
ArcPy: it creates a projected FileGDB, performs real geoprocessing (Buffer,
Project, AddField, DeleteField), and then runs the **real**
``arcmap_runtime_py2.semantic_acceptance`` evaluators against the real outputs.
Each scenario injects one semantic fault and checks that the independent
evaluator catches it while accepting the correct result.

Run with the ArcMap Python 2.7 interpreter::

    & "C:\\Python27\\ArcGIS10.2\\python.exe" tests/python2_runtime/real_arcmap_case_py2.py
"""
from __future__ import absolute_import

import os
import shutil
import sys
import tempfile

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import arcpy  # noqa: E402
from arcmap_runtime_py2 import acceptance_probe  # noqa: E402
from arcmap_runtime_py2 import context_reader  # noqa: E402
from arcmap_runtime_py2 import semantic_acceptance  # noqa: E402

UTM = 32650


def _square(x0, y0, size):
    ring = arcpy.Array([
        arcpy.Point(x0, y0), arcpy.Point(x0 + size, y0),
        arcpy.Point(x0 + size, y0 + size), arcpy.Point(x0, y0 + size),
        arcpy.Point(x0, y0)])
    return arcpy.Polygon(ring)


def _build_source(gdb):
    sr = arcpy.SpatialReference(UTM)
    arcpy.CreateFeatureclass_management(gdb, "src", "POLYGON", spatial_reference=sr)
    path = os.path.join(gdb, "src")
    with arcpy.da.InsertCursor(path, ["SHAPE@"]) as cursor:
        cursor.insertRow([_square(0, 0, 10)])
        cursor.insertRow([_square(100, 100, 20)])
    return path


def _evaluate(rule, result_path):
    return semantic_acceptance.evaluate(rule, {"canonical_path": result_path})


def _buffer_rule(source, value):
    return {
        "proof_id": u"buffer.distance",
        "predicate": {"kind": "buffer", "distance": {
            "value": value, "unit": u"meters", "tolerance": 0.0}},
        "bindings": {"source": source, "result": u"__output__",
                     "crs_strategy": {"mode": "native_projected",
                                      "buffer_value": value}},
    }


def run_cases(gdb, source):
    cases = []

    def record(name, expected, verdict):
        cases.append({"scenario": name, "expected": expected,
                      "status": verdict.get("status"), "reason": verdict.get("reason", "")})

    correct = os.path.join(gdb, "buf_correct")
    arcpy.Buffer_analysis(source, correct, "100 Meters")
    record("buffer_correct", "Proven", _evaluate(_buffer_rule(source, 100.0), correct))

    wrong = os.path.join(gdb, "buf_wrong")
    arcpy.Buffer_analysis(source, wrong, "100000 Meters")
    record("buffer_wrong_distance", "Violated",
           _evaluate(_buffer_rule(source, 100.0), wrong))

    projected = os.path.join(gdb, "proj")
    arcpy.Project_management(source, projected, arcpy.SpatialReference(4326))
    expected_crs = arcpy.Describe(projected).spatialReference.name
    project_rule = {"proof_id": u"project.crs", "predicate": {
        "kind": "project", "spatial_reference": expected_crs},
        "bindings": {"result": u"__output__"}}
    record("project_correct", "Proven", _evaluate(project_rule, projected))

    not_projected = os.path.join(gdb, "not_proj")
    arcpy.CopyFeatures_management(source, not_projected)
    record("project_wrong_crs", "Violated", _evaluate(project_rule, not_projected))

    field_ok = os.path.join(gdb, "field_ok")
    arcpy.CopyFeatures_management(source, field_ok)
    arcpy.AddField_management(field_ok, "remark", "TEXT", field_length=50)
    field_rule = {"proof_id": u"field_add.remark", "predicate": {
        "kind": "field_add", "field_name": u"remark", "field_type": u"String"},
        "bindings": {"result": u"__output__"}}
    record("field_add_correct", "Proven", _evaluate(field_rule, field_ok))

    field_bad = os.path.join(gdb, "field_bad")
    arcpy.CopyFeatures_management(source, field_bad)
    arcpy.AddField_management(field_bad, "remark", "LONG")
    record("field_add_wrong_type", "Violated", _evaluate(field_rule, field_bad))

    field_missing = os.path.join(gdb, "field_missing")
    arcpy.CopyFeatures_management(source, field_missing)
    record("field_add_absent", "Violated", _evaluate(field_rule, field_missing))

    deleted = os.path.join(gdb, "deleted")
    arcpy.CopyFeatures_management(source, deleted)
    arcpy.AddField_management(deleted, "temp", "TEXT", field_length=10)
    arcpy.DeleteField_management(deleted, "temp")
    delete_rule = {"proof_id": u"field_delete.temp", "predicate": {
        "kind": "field_delete", "field_name": u"temp"},
        "bindings": {"result": u"__output__"}}
    record("field_delete_correct", "Proven", _evaluate(delete_rule, deleted))

    retained = os.path.join(gdb, "retained")
    arcpy.CopyFeatures_management(source, retained)
    arcpy.AddField_management(retained, "temp", "TEXT", field_length=10)
    record("field_delete_still_present", "Violated", _evaluate(delete_rule, retained))

    digest = context_reader.layer_content_digest(source)
    preserve_rule = {
        "proof_id": u"source.preserved",
        "predicate": {"kind": "source_preserved"},
        "bindings": {"source": source,
                     "source_manifest": {"source_content_digest": digest}}}
    record("source_preserved_ok", "Proven", _evaluate(preserve_rule, source))

    arcpy.AddField_management(source, "tamper", "LONG")
    record("source_modified", "Violated", _evaluate(preserve_rule, source))

    probe_doc = acceptance_probe.probe(u"buf_correct", u"feature_class", unicode(correct))
    probe_bad = acceptance_probe.probe(u"buf_wrong", u"feature_class", unicode(wrong))
    good_contract = {"rules": [dict(_buffer_rule(source, 100.0),
                                    output_id=u"buf_correct")]}
    bad_contract = {"rules": [dict(_buffer_rule(source, 100.0),
                                   output_id=u"buf_wrong")]}
    record("probe_contract_correct", "Proven",
           acceptance_probe.probe_contract(good_contract, probe_doc)[0])
    record("probe_contract_wrong_distance", "Violated",
           acceptance_probe.probe_contract(bad_contract, probe_bad)[0])

    probe_info = {
        "feature_count": probe_doc["feature_count"],
        "geometry": probe_doc["geometry"],
        "spatial_reference": probe_doc["spatial_reference"],
        "manifest_digest": probe_doc["manifest_digest"][:16],
        "wrong_digest": probe_bad["manifest_digest"][:16],
        "digests_differ": probe_doc["manifest_digest"] != probe_bad["manifest_digest"],
    }
    return cases, probe_info


def main():
    root = tempfile.mkdtemp(prefix="harness-real-arcmap-")
    gdb = os.path.join(root, "case.gdb")
    try:
        arcpy.CreateFileGDB_management(root, "case.gdb")
        source = _build_source(gdb)
        cases, probe_info = run_cases(gdb, source)
        print("Real ArcGIS 10.2 acceptance case study")
        print("%-28s %-9s %-9s %s" % ("scenario", "expected", "verdict", "reason"))
        failures = 0
        for case in cases:
            ok = case["status"] == case["expected"]
            failures += 0 if ok else 1
            print("%-28s %-9s %-9s %s" % (case["scenario"], case["expected"],
                                          case["status"], case["reason"]))
        print("\nprobe evidence: count=%d geometry=%s sr=%s"
              % (probe_info["feature_count"], probe_info["geometry"],
                 probe_info["spatial_reference"]))
        print("content-addressed digests differ (correct vs wrong): %s (%s != %s)"
              % (probe_info["digests_differ"], probe_info["manifest_digest"],
                 probe_info["wrong_digest"]))
        print("\n%d/%d scenarios matched" % (len(cases) - failures, len(cases)))
        return 1 if failures else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())


