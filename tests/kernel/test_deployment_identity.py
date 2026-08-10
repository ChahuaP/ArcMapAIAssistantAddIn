import json
import tempfile
import unittest
from pathlib import Path

from gateway_py3.runtime.deployment_identity import read_identity


class DeploymentIdentityTest(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "deployment_identity.json"

    def test_lowercase_sha256_is_accepted(self):
        self.path.write_text(json.dumps({"deployment_hash": "a" * 64}), encoding="utf-8")
        self.assertEqual(read_identity(self.path), "a" * 64)

    def test_missing_invalid_and_mismatched_schema_fail_closed(self):
        with self.assertRaises(RuntimeError):
            read_identity(self.path)
        self.path.write_text(json.dumps({"deployment_hash": "A" * 64}), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            read_identity(self.path)
        self.path.write_text(json.dumps({"deployment_hash": "a" * 64, "version": "x"}), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            read_identity(self.path)
