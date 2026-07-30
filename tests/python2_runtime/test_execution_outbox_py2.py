# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import shutil
import sys
import tempfile
import unittest
import uuid

try:
    unicode
except NameError:
    unicode = str


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from arcmap_runtime_py2.execution_outbox import ExecutionOutbox


TARGET = {"bridge_pid": 7, "bridge_port": 8766, "arcmap_pid": 70, "hwnd": 9}


class _Client(object):
    def __init__(self):
        self.calls = []

    def complete_run(self, *args):
        self.calls.append(args)


class ExecutionOutboxPython27Tests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp(prefix="geopilot-outbox-")

    def tearDown(self):
        shutil.rmtree(self.directory)

    def test_byte_uuid_owner_is_normalized_and_delivered(self):
        outbox = ExecutionOutbox(self.directory)
        run_id = str(uuid.uuid4())
        owner_id = str(uuid.uuid4())
        entry = outbox.enqueue(run_id, owner_id, "failed", {
            "ok": False, "traceback": "ArcPy traceback",
        }, TARGET)
        self.assertTrue(isinstance(entry["run_id"], unicode))
        self.assertTrue(isinstance(entry["owner"], unicode))
        client = _Client()
        self.assertTrue(outbox.deliver(entry, client))
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][0], unicode(run_id))
        self.assertEqual(client.calls[0][3], unicode(owner_id))

    def test_expired_a_release_cannot_delete_b_and_c_is_blocked(self):
        outbox = ExecutionOutbox(self.directory)
        run_id = str(uuid.uuid4())
        owner_a = str(uuid.uuid4())
        owner_b = str(uuid.uuid4())
        owner_c = str(uuid.uuid4())
        self.assertTrue(outbox._acquire_delivery_lease(run_id, owner_a, now=0))
        self.assertTrue(outbox._acquire_delivery_lease(run_id, owner_b, now=30))
        outbox._release_delivery_lease(run_id, owner_a)
        self.assertFalse(outbox._acquire_delivery_lease(run_id, owner_c, now=31))
        self.assertEqual(outbox._read_lease(outbox._lease_path(run_id))["owner"], owner_b)
        outbox._release_delivery_lease(run_id, owner_b)


if __name__ == "__main__":
    unittest.main()
