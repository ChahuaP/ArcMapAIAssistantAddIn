# -*- coding: utf-8 -*-
"""End-to-end semantic_acceptance evaluator tests under the real ArcGIS Py2.7.

These build real scratch feature classes / rasters through ArcPy and run the
production evaluators on them, proving success and counterexample (Violation)
on the same rule ABI the Gateway seals.  Run with the ArcGIS Python 2.7::

  C:/Python27/ArcGIS10.2/python.exe -m unittest discover \
      -s tests/python2_runtime -p "test_semantic_acceptance_py2.py"
"""
from __future__ import absolute_import

import os
import sys
import json
import shutil
import tempfile
import unittest
import importlib

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _load_real_arcpy():
    # Always (re)import the real installed ArcPy fresh, dropping any stub that a
    # sibling test module installed into sys.modules.  Returns None when real
    # ArcPy is not importable (e.g. running under Python 3).
    for key in list(sys.modules):
        if key == "arcpy" or key.startswith("arcpy."):
            del sys.modules[key]
    try:
        import arcpy as _real
        return _real
    except ImportError:
        return None


arcpy = _load_real_arcpy()


def setUpModule():
    global arcpy
    # Other Py2 test modules re-install an arcpy stub during their own test run
    # (after our import).  Restore the real ArcPy here — immediately before our
    # tests run — then reload the runtime modules so their module-level
    # ``import arcpy`` rebinds to the real package.  Py2 builtin ``reload``.
    arcpy = _load_real_arcpy()
    if arcpy is None:
        raise unittest.SkipTest("real ArcPy is not installed in this interpreter")
    reload(importlib.import_module("arcmap_runtime_py2.context_reader"))
    # condition_utils is consumed by the field/filter evaluators; a real load
    # failure must surface, not be swallowed.
    reload(importlib.import_module("arcmap_runtime_py2.operations.condition_utils"))
    reload(importlib.import_module("arcmap_runtime_py2.semantic_acceptance"))


from arcmap_runtime_py2 import semantic_acceptance, context_reader  # noqa: E402


def _transport(rule):
    return json.loads(json.dumps(rule))


def _fs(name, ftype="string"):
    return {"name": name, "type": ftype, "nullable": True, "length": None,
            "precision": None, "scale": None, "domain": []}


def _sr():
    return arcpy.SpatialReference(4326)


def _rect(xmin, ymin, xmax, ymax, sr):
    ring = arcpy.Array([arcpy.Point(x, y) for x, y in
                        [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax), (xmin, ymin)]])
    return arcpy.Polygon(arcpy.Array([ring]), sr)


def _add_fields(fc, fields):
    for name, ftype in fields:
        if ftype == "TEXT":
            arcpy.AddField_management(fc, name, "TEXT", field_length=50)
        else:
            arcpy.AddField_management(fc, name, ftype)


def _polygons(gdb, name, fields, rows):
    sr = _sr()
    fc = os.path.join(gdb, name)
    arcpy.CreateFeatureclass_management(gdb, name, "POLYGON", spatial_reference=sr)
    _add_fields(fc, fields)
    field_names = [n for n, _t in fields]
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + field_names) as cursor:
        for shape, attrs in rows:
            cursor.insertRow([shape] + [attrs.get(n) for n in field_names])
    return fc


def _points(gdb, name, fields, rows):
    sr = _sr()
    fc = os.path.join(gdb, name)
    arcpy.CreateFeatureclass_management(gdb, name, "POINT", spatial_reference=sr)
    _add_fields(fc, fields)
    field_names = [n for n, _t in fields]
    with arcpy.da.InsertCursor(fc, ["SHAPE@"] + field_names) as cursor:
        for shape, attrs in rows:
            cursor.insertRow([shape] + [attrs.get(n) for n in field_names])
    return fc


def _binding(path, identity_fields, field_names):
    return {"path": path, "identity_fields": identity_fields,
            "fields": [_fs(n) for n in field_names]}


class _Scratch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix=u"geopilot_eval_")
        self.gdb = os.path.join(self._tmp, u"eval.gdb")
        arcpy.CreateFileGDB_management(self._tmp, u"eval.gdb")

    def tearDown(self):
        try:
            arcpy.ClearWorkspaceCache_management()
        except Exception:
            pass
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _doc(self, path):
        return {"output_id": u"output:r", "canonical_path": path, "exists": True,
                "kind": u"feature_class"}


class CopyEvaluatorTest(_Scratch):
    def setUp(self):
        super(CopyEvaluatorTest, self).setUp()
        sr = _sr()
        self.src = _polygons(self.gdb, u"src", [("TYPE", "TEXT")],
                             [(_rect(0, 0, 1, 1, sr), {"TYPE": u"a"}),
                              (_rect(5, 0, 6, 1, sr), {"TYPE": u"b"})])
        self.result = os.path.join(self.gdb, u"res")

    def _rule(self):
        return _transport({"proof_id": "acceptance:r",
                           "predicate": {"kind": "copy", "subject": "output:r", "source": "input:src"},
                           "bindings": {"source": _binding(self.src, ["OBJECTID"], ["TYPE"]),
                                        "subject": "__output__"}})

    def test_copy_of_source_is_proven(self):
        arcpy.Copy_management(self.src, self.result)
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Proven")

    def test_copy_missing_feature_is_violated(self):
        arcpy.Copy_management(self.src, self.result)
        with arcpy.da.UpdateCursor(self.result, ["OID@"]) as cursor:
            for row in cursor:
                cursor.deleteRow()
                break
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Violated")

    def test_copy_reshaped_geometry_is_violated(self):
        arcpy.Copy_management(self.src, self.result)
        sr = _sr()
        with arcpy.da.UpdateCursor(self.result, ["SHAPE@"]) as cursor:
            for row in cursor:
                cursor.updateRow([_rect(100, 0, 101, 1, sr)])
                break
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Violated")


class SourcePreservedDigestTest(_Scratch):
    def setUp(self):
        super(SourcePreservedDigestTest, self).setUp()
        sr = _sr()
        self.src = _polygons(self.gdb, u"src", [("TYPE", "TEXT")],
                             [(_rect(0, 0, 1, 1, sr), {"TYPE": u"a"}),
                              (_rect(5, 0, 6, 1, sr), {"TYPE": u"b"})])

    def test_feature_manifest_detects_geometry_change(self):
        before = context_reader.feature_manifest_digest(self.src, ["OBJECTID"])
        self.assertIsNotNone(before)
        sr = _sr()
        with arcpy.da.UpdateCursor(self.src, ["SHAPE@"]) as cursor:
            for row in cursor:
                cursor.updateRow([_rect(200, 0, 201, 1, sr)])
                break
        after = context_reader.feature_manifest_digest(self.src, ["OBJECTID"])
        self.assertIsNotNone(after)
        self.assertNotEqual(before, after)

    def test_source_preserved_proven_and_violated(self):
        digest = context_reader.feature_manifest_digest(self.src, ["OBJECTID"])
        rule = _transport({"proof_id": "acceptance:r",
                           "predicate": {"kind": "source_preserved", "subject": "input:src"},
                           "bindings": {"source": _binding(self.src, ["OBJECTID"], ["TYPE"]),
                                        "source_manifest": {"feature_manifest_digest": digest}}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(self.src))["status"], "Proven")
        sr = _sr()
        with arcpy.da.UpdateCursor(self.src, ["SHAPE@"]) as cursor:
            for row in cursor:
                cursor.updateRow([_rect(300, 0, 301, 1, sr)])
                break
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(self.src))["status"], "Violated")


class AggregateEvaluatorTest(_Scratch):
    def setUp(self):
        super(AggregateEvaluatorTest, self).setUp()
        sr = _sr()
        self.src = _polygons(self.gdb, u"src", [("TYPE", "TEXT")],
                             [(_rect(0, 0, 1, 1, sr), {"TYPE": u"a"}),
                              (_rect(5, 0, 6, 1, sr), {"TYPE": u"b"}),
                              (_rect(10, 0, 11, 1, sr), {"TYPE": u"a"})])
        self.result = os.path.join(self.gdb, u"res")

    def _rule(self):
        return _transport({"proof_id": "acceptance:r",
                           "predicate": {"kind": "aggregate", "subject": "output:r",
                                         "source": "input:src", "dissolve_fields": ["TYPE"]},
                           "bindings": {"source": _binding(self.src, ["OBJECTID"], ["TYPE"]),
                                        "subject": "__output__",
                                        "required_fields": [_fs("TYPE")],
                                        "aggregate": {"dissolve_fields": ["TYPE"], "statistics": [], "tolerance": 0.0}}})

    def test_dissolve_grouping_is_proven(self):
        arcpy.Dissolve_management(self.src, self.result, ["TYPE"])
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Proven")

    def test_dissolve_with_dropped_group_is_violated(self):
        arcpy.Dissolve_management(self.src, self.result, ["TYPE"])
        with arcpy.da.UpdateCursor(self.result, ["OID@"]) as cursor:
            for row in cursor:
                cursor.deleteRow()
                break
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Violated")


class SpatialJoinEvaluatorTest(_Scratch):
    def setUp(self):
        super(SpatialJoinEvaluatorTest, self).setUp()
        sr = _sr()
        self.target = _polygons(self.gdb, u"tgt", [("TNAME", "TEXT")],
                                [(_rect(0, 0, 10, 10, sr), {"TNAME": u"t1"}),
                                 (_rect(20, 0, 30, 10, sr), {"TNAME": u"t2"})])
        self.join = _points(self.gdb, u"join", [("JNAME", "TEXT")],
                            [(arcpy.PointGeometry(arcpy.Point(5, 5), sr), {"JNAME": u"j1"}),
                             (arcpy.PointGeometry(arcpy.Point(25, 5), sr), {"JNAME": u"j2"})])
        self.result = os.path.join(self.gdb, u"res")

    def _rule(self, join_fields=("JNAME",)):
        return _transport({"proof_id": "acceptance:r",
                           "predicate": {"kind": "spatial_join", "subject": "output:r",
                                         "target": "input:tgt", "join": "input:join"},
                           "bindings": {"target": _binding(self.target, ["OBJECTID"], ["TNAME"]),
                                        "join": _binding(self.join, ["OBJECTID"], list(join_fields)),
                                        "subject": "__output__",
                                        "required_fields": [_fs("Join_Count", "integer"), _fs("TARGET_FID", "integer")],
                                        "join_spec": {"match_option": "intersect",
                                                      "target_identity": ["OBJECTID"],
                                                      "join_identity": ["OBJECTID"],
                                                      "join_fields": list(join_fields),
                                                      "target_fid_field": "TARGET_FID"}}})

    def test_spatial_join_is_proven(self):
        arcpy.SpatialJoin_analysis(self.target, self.join, self.result, join_operation="JOIN_ONE_TO_ONE")
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Proven")

    def test_wrong_join_count_is_violated(self):
        arcpy.SpatialJoin_analysis(self.target, self.join, self.result, join_operation="JOIN_ONE_TO_ONE")
        with arcpy.da.UpdateCursor(self.result, ["Join_Count"]) as cursor:
            for row in cursor:
                cursor.updateRow([99])
                break
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Violated")

    def test_fabricated_joined_value_is_violated(self):
        arcpy.SpatialJoin_analysis(self.target, self.join, self.result, join_operation="JOIN_ONE_TO_ONE")
        with arcpy.da.UpdateCursor(self.result, ["JNAME"]) as cursor:
            for row in cursor:
                cursor.updateRow([u"FAKE"])
                break
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Violated")


class OverlayEvaluatorTest(_Scratch):
    def setUp(self):
        super(OverlayEvaluatorTest, self).setUp()
        sr = _sr()
        self.a = _polygons(self.gdb, u"a", [("FA", "TEXT")],
                           [(_rect(0, 0, 10, 10, sr), {"FA": u"a1"})])
        self.b = _polygons(self.gdb, u"b", [("FB", "TEXT")],
                           [(_rect(5, 0, 15, 10, sr), {"FB": u"b1"})])
        self.result = os.path.join(self.gdb, u"res")

    def _rule(self, method):
        return _transport({"proof_id": "acceptance:r",
                           "predicate": {"kind": "overlay", "subject": "output:r", "method": method,
                                         "sources": ["input:a", "input:b"]},
                           "bindings": {"sources": [_binding(self.a, ["OBJECTID"], ["FA"]),
                                                    _binding(self.b, ["OBJECTID"], ["FB"])],
                                        "subject": "__output__",
                                        "overlay": {"method": method, "tolerance": 0.0,
                                                    "static_fields": [],
                                                    "field_mapping": [
                                                        {"namespace": "source_0", "identity_fields": ["OBJECTID"], "fields": ["FA"]},
                                                        {"namespace": "source_1", "identity_fields": ["OBJECTID"], "fields": ["FB"]}]}}})

    def test_intersect_is_proven(self):
        arcpy.Intersect_analysis([self.a, self.b], self.result)
        self.assertEqual(semantic_acceptance.evaluate(self._rule("intersect"), self._doc(self.result))["status"], "Proven")

    def test_clip_is_proven(self):
        arcpy.Clip_analysis(self.a, self.b, self.result)
        self.assertEqual(semantic_acceptance.evaluate(self._rule("clip"), self._doc(self.result))["status"], "Proven")

    def test_union_is_proven(self):
        arcpy.Union_analysis([self.a, self.b], self.result)
        self.assertEqual(semantic_acceptance.evaluate(self._rule("union"), self._doc(self.result))["status"], "Proven")

    def test_erase_is_proven(self):
        arcpy.Erase_analysis(self.a, self.b, self.result)
        self.assertEqual(semantic_acceptance.evaluate(self._rule("erase"), self._doc(self.result))["status"], "Proven")

    def test_intersect_with_extra_fragment_is_violated(self):
        arcpy.Intersect_analysis([self.a, self.b], self.result)
        sr = _sr()
        with arcpy.da.InsertCursor(self.result, ["SHAPE@", "FA", "FB"]) as cursor:
            cursor.insertRow([_rect(1000, 1000, 1001, 1001, sr), u"a1", u"b1"])
        self.assertEqual(semantic_acceptance.evaluate(self._rule("intersect"), self._doc(self.result))["status"], "Violated")


class MergeEvaluatorTest(_Scratch):
    def setUp(self):
        super(MergeEvaluatorTest, self).setUp()
        sr = _sr()
        self.a = _polygons(self.gdb, u"a", [("FA", "TEXT")],
                           [(_rect(0, 0, 1, 1, sr), {"FA": u"a1"}),
                            (_rect(2, 0, 3, 1, sr), {"FA": u"a2"})])
        self.b = _polygons(self.gdb, u"b", [("FB", "TEXT")],
                           [(_rect(5, 0, 6, 1, sr), {"FB": u"b1"})])
        self.result = os.path.join(self.gdb, u"res")

    def _rule(self):
        return _transport({"proof_id": "acceptance:r",
                           "predicate": {"kind": "merge", "subject": "output:r", "sources": ["input:a", "input:b"]},
                           "bindings": {"sources": [_binding(self.a, ["OBJECTID"], ["FA"]),
                                                    _binding(self.b, ["OBJECTID"], ["FB"])],
                                        "subject": "__output__"}})

    def test_merge_is_proven(self):
        arcpy.Merge_management([self.a, self.b], self.result)
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Proven")

    def test_merge_last_column_changed_is_violated(self):
        arcpy.Merge_management([self.a, self.b], self.result)
        with arcpy.da.UpdateCursor(self.result, ["FA"]) as cursor:
            for row in cursor:
                cursor.updateRow([u"CHANGED"])
                break
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Violated")

    def test_merge_geometry_changed_is_violated(self):
        arcpy.Merge_management([self.a, self.b], self.result)
        sr = _sr()
        with arcpy.da.UpdateCursor(self.result, ["SHAPE@"]) as cursor:
            for row in cursor:
                cursor.updateRow([_rect(500, 0, 501, 1, sr)])
                break
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.result))["status"], "Violated")


class AppendEvaluatorTest(_Scratch):
    def setUp(self):
        super(AppendEvaluatorTest, self).setUp()
        sr = _sr()
        self.target = _polygons(self.gdb, u"tgt", [("FA", "TEXT")],
                                [(_rect(0, 0, 1, 1, sr), {"FA": u"t1"})])
        self.source = _polygons(self.gdb, u"src", [("FA", "TEXT")],
                                [(_rect(9, 0, 10, 1, sr), {"FA": u"s1"})])

    def _rule(self):
        return _transport({"proof_id": "acceptance:r",
                           "predicate": {"kind": "append", "subject": "output:r",
                                         "sources": ["input:src"], "target": "input:tgt"},
                           "bindings": {"target": _binding(self.target, ["OBJECTID"], ["FA"]),
                                        "sources": [_binding(self.source, ["OBJECTID"], ["FA"])]}})

    def test_append_is_proven(self):
        arcpy.Append_management([self.source], self.target, "NO_TEST")
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.target))["status"], "Proven")

    def test_append_missing_source_feature_is_violated(self):
        # Target pre-state (one feature) frozen conceptually; if the appended
        # source feature is absent from the post-state target, it Violates.
        # Here we do NOT append, so the source feature is missing.
        self.assertEqual(semantic_acceptance.evaluate(self._rule(), self._doc(self.target))["status"], "Violated")


class RasterSourcePreservedTest(_Scratch):
    def setUp(self):
        super(RasterSourcePreservedTest, self).setUp()
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy unavailable for raster tests")
        self.np = np
        # NumPyArrayToRaster persists reliably to a folder TIFF (a FileGDB raster
        # path does not round-trip through Describe in 10.2 without CopyRaster).
        self.raster_path = os.path.join(self._tmp, u"ras.tif")

    def _make_raster(self, center_value):
        np = self.np
        array = np.array([[1, 2, 3], [4, center_value, 6], [7, 8, 9]], dtype="int16")
        raster = arcpy.NumPyArrayToRaster(array, arcpy.Point(0.0, 0.0), 1.0, 1.0, -9999)
        raster.save(self.raster_path)

    def test_raster_single_cell_change_is_violated(self):
        self._make_raster(5)
        before = context_reader.raster_content_digest(self.raster_path)
        if before is None:
            self.skipTest("ArcGIS 10.2 cannot read raster cells on this runtime")
        rule = _transport({"proof_id": "acceptance:r",
                           "predicate": {"kind": "source_preserved", "subject": "input:ras"},
                           "bindings": {"source": {"path": self.raster_path},
                                        "source_manifest": {"raster_content_digest": before}}})
        # Recreate the raster with one cell changed; the sealed digest must mismatch.
        if arcpy.Exists(self.raster_path):
            arcpy.Delete_management(self.raster_path)
        self._make_raster(999)
        proof = semantic_acceptance.evaluate(rule, self._doc(self.raster_path))
        self.assertEqual(proof["status"], "Violated", proof)

    def test_raster_unchanged_is_proven(self):
        self._make_raster(5)
        digest = context_reader.raster_content_digest(self.raster_path)
        if digest is None:
            self.skipTest("ArcGIS 10.2 cannot read raster cells on this runtime")
        rule = _transport({"proof_id": "acceptance:r",
                           "predicate": {"kind": "source_preserved", "subject": "input:ras"},
                           "bindings": {"source": {"path": self.raster_path},
                                        "source_manifest": {"raster_content_digest": digest}}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(self.raster_path))["status"], "Proven")


class BufferEvaluatorTest(_Scratch):
    def _buffer_rule(self, src, distance_qty, strategy):
        return _transport({"proof_id": "a",
                           "predicate": {"kind": "buffer", "subject": "o", "source": "s",
                                         "distance": distance_qty},
                           "bindings": {"source": _binding(src, ["OBJECTID"], []),
                                        "subject": "__output__", "crs_strategy": strategy}})

    def test_projected_meters_buffer_proven_and_wrong(self):
        sr = arcpy.SpatialReference(3395)  # World Mercator, meters
        src = os.path.join(self.gdb, u"src")
        arcpy.CreateFeatureclass_management(self.gdb, u"src", "POLYGON", spatial_reference=sr)
        with arcpy.da.InsertCursor(src, ["SHAPE@"]) as cursor:
            cursor.insertRow([_rect(0.0, 0.0, 1.0, 1.0, sr)])
        qty = {"value": 10, "unit": "meters", "dimension": "length", "tolerance": 2.0, "crs": None}
        # meters_per_unit = 1.0 -> buffer_value = 10 (native Mercator meters).
        strategy = {"mode": "native_projected", "buffer_value": 10.0,
                    "meters": 10.0, "meters_per_unit": 1.0, "source_crs": {"name": "EPSG:3395", "type": "Projected"}}
        good = os.path.join(self.gdb, u"good")
        arcpy.Buffer_analysis(src, good, "10 Meters")
        self.assertEqual(semantic_acceptance.evaluate(self._buffer_rule(src, qty, strategy), self._doc(good))["status"], "Proven")
        bad = os.path.join(self.gdb, u"bad")
        arcpy.Buffer_analysis(src, bad, "50 Meters")
        self.assertEqual(semantic_acceptance.evaluate(self._buffer_rule(src, qty, strategy), self._doc(bad))["status"], "Violated")

    def test_projected_foot_buffer_proven(self):
        # A foot-based projected CRS: the sealed meters_per_unit converts the
        # meters Quantity into native feet so the comparison stays in-CRS.
        sr = arcpy.SpatialReference(2232)  # NAD83 StatePlane Colorado Central (ft)
        if sr.name == u"Unknown":
            self.skipTest("foot StatePlane CRS unavailable in this ArcGIS")
        # Read the real meters-per-unit from the SR (what the seal would seal).
        mpu = getattr(sr, "metersPerUnit", None) or 0.3048
        src = os.path.join(self.gdb, u"src")
        arcpy.CreateFeatureclass_management(self.gdb, u"src", "POLYGON", spatial_reference=sr)
        with arcpy.da.InsertCursor(src, ["SHAPE@"]) as cursor:
            cursor.insertRow([_rect(500000.0, 500000.0, 500100.0, 500100.0, sr)])
        qty = {"value": 30, "unit": "meters", "dimension": "length", "tolerance": 5.0, "crs": None}
        strategy = {"mode": "native_projected", "buffer_value": 30.0 / float(mpu),
                    "meters": 30.0, "meters_per_unit": float(mpu),
                    "source_crs": {"name": sr.name, "type": "Projected"}}
        good = os.path.join(self.gdb, u"good")
        arcpy.Buffer_analysis(src, good, "30 Meters")
        self.assertEqual(semantic_acceptance.evaluate(self._buffer_rule(src, qty, strategy), self._doc(good))["status"], "Proven")


class FieldEvaluatorTest(_Scratch):
    def setUp(self):
        super(FieldEvaluatorTest, self).setUp()
        sr = _sr()
        self.src = _polygons(self.gdb, u"src", [("TYPE", "TEXT")], [(_rect(0, 0, 1, 1, sr), {"TYPE": u"a"})])

    def test_field_add_presence_is_proven(self):
        arcpy.AddField_management(self.src, "NEWFLAG", "TEXT", field_length=20)
        rule = _transport({"proof_id": "a", "predicate": {"kind": "field_add", "subject": "o", "target": "t", "field_name": "NEWFLAG", "field_type": "string"},
                           "bindings": {"target": {"path": self.src}}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(self.src))["status"], "Proven")

    def test_field_delete_absence_is_proven(self):
        rule = _transport({"proof_id": "a", "predicate": {"kind": "field_delete", "subject": "o", "target": "t", "field_name": "ABSENT", "field_type": "string"},
                           "bindings": {"target": {"path": self.src}}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(self.src))["status"], "Proven")

    def test_field_update_value_is_proven_and_wrong_is_violated(self):
        arcpy.CalculateField_management(self.src, "TYPE", "'done'", "PYTHON")
        rule = _transport({"proof_id": "a", "predicate": {"kind": "field_update", "subject": "o", "target": "t",
                                                          "where": {"op": "is_not_null", "field": "TYPE"},
                                                          "assignments": {"TYPE": u"done"}},
                           "bindings": {"target": {"path": self.src}}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(self.src))["status"], "Proven")
        arcpy.CalculateField_management(self.src, "TYPE", "'other'", "PYTHON")
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(self.src))["status"], "Violated")


class ProjectEvaluatorTest(_Scratch):
    def test_project_crs_is_proven(self):
        sr = _sr()
        src = _polygons(self.gdb, u"src", [], [(_rect(0, 0, 1, 1, sr), {})])
        out = os.path.join(self.gdb, u"out")
        target_sr = arcpy.SpatialReference(3395)  # World Mercator (meters)
        arcpy.Project_management(src, out, target_sr)
        rule = _transport({"proof_id": "a", "predicate": {"kind": "project", "subject": "o", "source": "s", "spatial_reference": "Mercator"},
                           "bindings": {"source": _binding(src, ["OBJECTID"], []), "subject": "__output__"}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(out))["status"], "Proven")


class FeatureFamilyEvaluatorTest(_Scratch):
    def test_feature_create_is_proven_and_empty_is_violated(self):
        sr = _sr()
        out = os.path.join(self.gdb, u"fc")
        _polygons(self.gdb, u"fc", [], [(_rect(0, 0, 1, 1, sr), {})])
        rule = _transport({"proof_id": "a", "predicate": {"kind": "feature_create", "subject": "o", "action": "create"},
                           "bindings": {"subject": "__output__"}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(out))["status"], "Proven")
        empty = os.path.join(self.gdb, u"empty")
        arcpy.CreateFeatureclass_management(self.gdb, u"empty", "POLYGON", spatial_reference=sr)  # no features
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(empty))["status"], "Violated")

    def test_inspect_is_proven(self):
        sr = _sr()
        src = _polygons(self.gdb, u"src", [], [(_rect(0, 0, 1, 1, sr), {})])
        rule = _transport({"proof_id": "a", "predicate": {"kind": "inspect", "subject": "s", "target": "t"},
                           "bindings": {"subject": {"path": src}}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(src))["status"], "Proven")


class ArtifactExportEvaluatorTest(_Scratch):
    def test_csv_export_is_proven(self):
        import csv as _csv
        path = os.path.join(self._tmp, u"out.csv")
        with open(path, "wb") as handle:
            writer = _csv.writer(handle)
            writer.writerow(["a", "b"])
            writer.writerow([1, 2])
        from shared_runtime.file_semantics import inspect_file
        semantics = inspect_file(path, "csv")
        doc = {"output_id": u"o", "canonical_path": path, "exists": True, "kind": u"file",
               "file_semantics": semantics}
        rule = _transport({"proof_id": "a", "predicate": {"kind": "artifact_export", "subject": "o",
                                                          "action": "export_table", "selected_only": False, "output_format": "csv"},
                           "bindings": {"subject": "__output__"}})
        self.assertEqual(semantic_acceptance.evaluate(rule, doc)["status"], "Proven")


class AttributeFilterEvaluatorTest(_Scratch):
    def test_attribute_filter_selection_is_proven(self):
        sr = _sr()
        src = _polygons(self.gdb, u"src", [("TYPE", "TEXT")],
                        [(_rect(0, 0, 1, 1, sr), {"TYPE": u"a"}),
                         (_rect(5, 0, 6, 1, sr), {"TYPE": u"b"})])
        where = {"op": "is_not_null", "field": "TYPE"}  # matches all features
        rule = _transport({"proof_id": "a", "predicate": {"kind": "attribute_filter", "subject": "s", "target": "t",
                                                          "where": where, "selection_type": "new_selection"},
                           "bindings": {"source": _binding(src, ["OBJECTID"], ["TYPE"]),
                                        "subject": "__output__", "identity_fields": ["OBJECTID"]}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(src))["status"], "Proven")


class AddXyEvaluatorTest(_Scratch):
    def test_add_xy_presence_proven_and_absent_violated(self):
        sr = _sr()
        pts = os.path.join(self.gdb, u"pts")
        arcpy.CreateFeatureclass_management(self.gdb, u"pts", "POINT", spatial_reference=sr)
        with arcpy.da.InsertCursor(pts, ["SHAPE@"]) as cursor:
            cursor.insertRow([arcpy.PointGeometry(arcpy.Point(5.0, 7.0), sr)])
        rule = _transport({"proof_id": "a", "predicate": {"kind": "add_xy", "subject": "o", "target": "t"},
                           "bindings": {"target": {"path": pts}}})
        # No AddXY run yet -> POINT_X/POINT_Y absent -> Violated.
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(pts))["status"], "Violated")
        arcpy.AddXY_management(pts)
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(pts))["status"], "Proven")


class RepairEvaluatorTest(_Scratch):
    def test_repair_clean_proven_and_self_intersect_violated(self):
        sr = _sr()
        clean = _polygons(self.gdb, u"clean", [], [(_rect(0, 0, 1, 1, sr), {})])
        rule = _transport({"proof_id": "a", "predicate": {"kind": "repair", "subject": "o", "target": "t"},
                           "bindings": {"target": {"path": clean}}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(clean))["status"], "Proven")
        # A null-geometry feature is flagged by CheckGeometry -> Violated.
        bad = os.path.join(self.gdb, u"bad")
        arcpy.CreateFeatureclass_management(self.gdb, u"bad", "POLYGON", spatial_reference=sr)
        with arcpy.da.InsertCursor(bad, ["SHAPE@"]) as cursor:
            cursor.insertRow([None])
        bad_rule = _transport({"proof_id": "a", "predicate": {"kind": "repair", "subject": "o", "target": "t"},
                               "bindings": {"target": {"path": bad}}})
        self.assertEqual(semantic_acceptance.evaluate(bad_rule, self._doc(bad))["status"], "Violated")


class SpatialFilterEvaluatorTest(_Scratch):
    def test_spatial_filter_full_coverage_proven_and_disjoint_violated(self):
        sr = _sr()
        src = _polygons(self.gdb, u"src", [("TYPE", "TEXT")],
                        [(_rect(0, 0, 1, 1, sr), {"TYPE": u"a"}),
                         (_rect(5, 0, 6, 1, sr), {"TYPE": u"b"})])
        # A selector that covers both source features -> every source intersects.
        selector = _polygons(self.gdb, u"sel", [], [(_rect(-1, -1, 7, 2, sr), {})])
        rule = _transport({"proof_id": "a", "predicate": {"kind": "spatial_filter", "subject": "s", "target": "t",
                                                          "selector": "sel", "overlap_type": "intersect",
                                                          "selection_type": "new_selection"},
                           "bindings": {"source": _binding(src, ["OBJECTID"], ["TYPE"]),
                                        "selector": _binding(selector, ["OBJECTID"], []),
                                        "subject": "__output__", "identity_fields": ["OBJECTID"]}})
        self.assertEqual(semantic_acceptance.evaluate(rule, self._doc(src))["status"], "Proven")
        # A selector far from the source: nothing intersects, so the result (the
        # full source set) must Violate.
        far = _polygons(self.gdb, u"far", [], [(_rect(1000, 1000, 1001, 1001, sr), {})])
        far_rule = _transport({"proof_id": "a", "predicate": {"kind": "spatial_filter", "subject": "s", "target": "t",
                                                              "selector": "sel", "overlap_type": "intersect",
                                                              "selection_type": "new_selection"},
                               "bindings": {"source": _binding(src, ["OBJECTID"], ["TYPE"]),
                                            "selector": _binding(far, ["OBJECTID"], []),
                                            "subject": "__output__", "identity_fields": ["OBJECTID"]}})
        self.assertEqual(semantic_acceptance.evaluate(far_rule, self._doc(src))["status"], "Violated")


if __name__ == "__main__":
    unittest.main()
