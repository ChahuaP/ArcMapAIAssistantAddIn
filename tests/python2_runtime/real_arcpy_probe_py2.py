# -*- coding: utf-8 -*-
from __future__ import absolute_import
import os, shutil, sys, tempfile, unittest
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path: sys.path.insert(0, REPO_ROOT)
import arcpy
from arcmap_runtime_py2 import acceptance_probe
class RealArcPyFileGdbProbeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="geopilot-real-gdb-"); self.gdb = os.path.join(self.root, "probe.gdb")
        arcpy.CreateFileGDB_management(self.root, "probe.gdb"); sr = arcpy.SpatialReference(4326)
        arcpy.CreateFeatureDataset_management(self.gdb, "fd", sr)
        arcpy.CreateFeatureclass_management(self.gdb, "root_fc", "POINT", spatial_reference=sr)
        arcpy.CreateFeatureclass_management(os.path.join(self.gdb, "fd"), "nested_fc", "POINT", spatial_reference=sr)
        arcpy.CreateTable_management(self.gdb, "root_table")
    def tearDown(self):
        try:
            if arcpy.Exists(self.gdb): arcpy.Delete_management(self.gdb)
        finally: shutil.rmtree(self.root, ignore_errors=True)
    def test_probe_unit_walks_real_root_and_feature_dataset(self):
        document = acceptance_probe.probe_unit(self.gdb)
        for name in ("root_fc", "root_table", "fd", "fd/nested_fc"): self.assertIn(name, document["datasets"])
        self.assertTrue(document["manifest_digest"])
        self.assertFalse(any(item["relative_path"].lower().endswith(".lock") for item in document["members"]))
