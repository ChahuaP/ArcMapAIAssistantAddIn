# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import shutil
import tempfile
import unittest
import hashlib
import json

from arcmap_runtime_py2 import runtime_gate


class _Field(object):
    def __init__(self, name):
        self.name = name


class _Cursor(object):
    def __init__(self, rows):
        self.rows = rows
    def __iter__(self):
        return iter(self.rows)


class RuntimeGatePython27Tests(unittest.TestCase):
    def test_protocol_mismatch_fails_before_any_arcmap_action(self):
        self.assertRaises(RuntimeError, runtime_gate.apply,
                          {"protocol": "geopilot-runtime-gate-v0", "mode": "prepare"})

    def test_deployment_mismatch_fails_before_any_arcmap_action(self):
        request = {
            "mode": "prepare", "protocol": runtime_gate.RUNTIME_GATE_PROTOCOL,
            "schema_hash": runtime_gate.SCHEMA_SHA256,
            "arcmap_pid": 1, "bridge_pid": 2, "bridge_port": 3, "hwnd": 4,
            "deployment_hash": "stale", "lifecycle_lease_id": "lease",
            "result_path": "result.json", "source_layers": ["source.shp"],
            "staging_gdb": "outputs.gdb",
        }
        original = runtime_gate.deployment_identity
        class Identity(object):
            @staticmethod
            def deployment_hash(): return "current"
        try:
            runtime_gate.deployment_identity = Identity()
            self.assertRaisesRegexp(RuntimeError, "deployment identity mismatch",
                                    runtime_gate.apply, request)
        finally:
            runtime_gate.deployment_identity = original

    def test_truth_evaluator_uses_the_explicit_output_path_and_id_field(self):
        calls = []
        class DA(object):
            @staticmethod
            def SearchCursor(path, fields):
                calls.append((path, fields))
                return _Cursor([(u"C2",), (u"C1",)])
        class ArcPy(object):
            da = DA()
            @staticmethod
            def Exists(path): return path == u"D:\\out.gdb\\affected"
            @staticmethod
            def ListFields(path): return [_Field("COMM_ID")]
        original = runtime_gate.arcpy
        original_manifest = runtime_gate.acceptance_probe.file_manifest
        try:
            runtime_gate.arcpy = ArcPy()
            members = [{"relative_path": "out.gdb/a", "size": 1, "sha256": "a" * 64}]
            runtime_gate.acceptance_probe.file_manifest = lambda path: members
            artifact = {
                "output_id": "affected", "kind": "feature_class", "output_format": "gdb",
                "destination_dataset_path": u"D:\\out.gdb\\affected",
                "destination_publish_unit_path": u"D:\\", "publication_kind": "file_gdb",
                "members": members, "semantic_evidence": {"kind": "feature_class"},
                "acceptance_evidence_hash": "b" * 64,
            }
            encoded = json.dumps(artifact, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
            artifact["evidence_hash"] = hashlib.sha256(encoded).hexdigest()
            request = {"expected": {"affected": {"field": "COMM_ID"}},
                       "artifacts": {"affected": artifact}}
            runtime_gate._evaluate(request)
            self.assertEqual({"affected": [u"C1", u"C2"]}, runtime_gate._truth_ids(request))
            self.assertEqual([(u"D:\\out.gdb\\affected", ["COMM_ID"])], calls)
            request["expected"]["affected"]["field"] = "WRONG"
            self.assertRaises(RuntimeError, runtime_gate._evaluate, request)
        finally:
            runtime_gate.arcpy = original
            runtime_gate.acceptance_probe.file_manifest = original_manifest

    def test_lifecycle_hash_excludes_only_server_owned_default_gdb(self):
        original = runtime_gate.context_reader
        class Reader(object):
            default = u"D:\\g2\\outputs.gdb"
            @classmethod
            def read_context(cls):
                return {"layers": [{"name": "cities"}], "default_gdb": cls.default,
                        "content_hash": "raw"}
            @staticmethod
            def context_hash(value):
                import hashlib, json
                return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()
        try:
            runtime_gate.context_reader = Reader
            first = runtime_gate._lifecycle_context_hash()
            Reader.default = u"D:\\g3\\outputs.gdb"
            self.assertEqual(first, runtime_gate._lifecycle_context_hash())
        finally:
            runtime_gate.context_reader = original

    def test_prepare_creates_and_binds_an_arm_private_file_gdb(self):
        root = tempfile.mkdtemp()
        workspace = os.path.join(root, "g2", "outputs.gdb")
        source = os.path.join(root, "cities.shp")
        open(source, "wb").close()
        created = []
        class Env(object):
            workspace = None
            scratchWorkspace = None
        class Frame(object): pass
        class Mxd(object):
            activeDataFrame = Frame()
            defaultGeodatabase = None
        mxd = Mxd()
        class Mapping(object):
            @staticmethod
            def MapDocument(value): return mxd
            @staticmethod
            def ListLayers(*args): return []
            @staticmethod
            def RemoveLayer(*args): pass
            @staticmethod
            def Layer(path): return path
            @staticmethod
            def AddLayer(*args): pass
        class ArcPy(object):
            env = Env()
            mapping = Mapping()
            @staticmethod
            def Exists(path): return os.path.exists(path) or path in created
            @staticmethod
            def CreateFileGDB_management(parent, name): created.append(os.path.join(parent, name))
        original_arcpy = runtime_gate.arcpy
        original_reader = runtime_gate.context_reader
        try:
            runtime_gate.arcpy = ArcPy()
            class Reader(object):
                @staticmethod
                def read_context(): return {"layers": [{}]}
            runtime_gate.context_reader = Reader()
            runtime_gate._prepare({"source_layers": [source], "staging_gdb": workspace})
            self.assertEqual([workspace], created)
            self.assertEqual(workspace, runtime_gate.arcpy.env.workspace)
            self.assertEqual(workspace, runtime_gate.arcpy.env.scratchWorkspace)
            self.assertEqual(workspace, mxd.defaultGeodatabase)
        finally:
            runtime_gate.arcpy = original_arcpy
            runtime_gate.context_reader = original_reader
            shutil.rmtree(root)


if __name__ == "__main__":
    unittest.main()
