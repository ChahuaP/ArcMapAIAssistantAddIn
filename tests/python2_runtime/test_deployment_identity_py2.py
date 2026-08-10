# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os
import shutil
import tempfile
import unittest

from arcmap_runtime_py2 import deployment_identity


class DeploymentIdentityPython27Tests(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(os.path.dirname(deployment_identity.__file__), "deployment_identity.json")
        self.backup = self.path + ".test-backup"
        if os.path.exists(self.path):
            shutil.copyfile(self.path, self.backup)

    def tearDown(self):
        if os.path.exists(self.path):
            os.remove(self.path)
        if os.path.exists(self.backup):
            shutil.move(self.backup, self.path)

    def test_missing_and_invalid_fail_closed(self):
        if os.path.exists(self.path):
            os.remove(self.path)
        self.assertRaises(RuntimeError, deployment_identity.deployment_hash)
        with open(self.path, "wb") as handle:
            handle.write(json.dumps({"deployment_hash": "A" * 64}))
        self.assertRaises(RuntimeError, deployment_identity.deployment_hash)

    def test_lowercase_sha256_is_accepted(self):
        with open(self.path, "wb") as handle:
            handle.write(json.dumps({"deployment_hash": "a" * 64}))
        self.assertEqual(deployment_identity.deployment_hash(), "a" * 64)
