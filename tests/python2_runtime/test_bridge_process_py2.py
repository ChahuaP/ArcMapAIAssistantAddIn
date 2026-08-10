# -*- coding: utf-8 -*-
from __future__ import absolute_import

import unittest

from arcmap_runtime_py2 import bridge_process


class _FakeClock(object):
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _FakeProcess(object):
    pid = 4321

    def __init__(self):
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


class BridgeProcessPython27Tests(unittest.TestCase):
    def test_arcgis_cold_start_can_take_more_than_ten_seconds(self):
        clock = _FakeClock()
        process = _FakeProcess()
        original_time = bridge_process.time
        original_popen = bridge_process.subprocess.Popen
        original_isfile = bridge_process.path_utils.isfile
        original_dirname = bridge_process.path_utils.dirname
        original_remove = bridge_process.path_utils.remove
        original_read_ready = bridge_process._read_ready_if_present
        original_is_healthy = bridge_process._is_healthy
        try:
            bridge_process.time = clock
            bridge_process.subprocess.Popen = lambda *args, **kwargs: process
            bridge_process.path_utils.isfile = lambda path: path == u"bridge.exe"
            bridge_process.path_utils.dirname = lambda path: u"."
            bridge_process.path_utils.remove = lambda path: None
            bridge_process._read_ready_if_present = lambda: (
                {"pid": process.pid, "port": 8766}
                if clock.now >= 12.0 else None
            )
            bridge_process._is_healthy = lambda ready: True

            ready = bridge_process.ensure_running(u"bridge.exe")

            self.assertEqual(ready["pid"], process.pid)
            self.assertFalse(process.terminated)
        finally:
            bridge_process.time = original_time
            bridge_process.subprocess.Popen = original_popen
            bridge_process.path_utils.isfile = original_isfile
            bridge_process.path_utils.dirname = original_dirname
            bridge_process.path_utils.remove = original_remove
            bridge_process._read_ready_if_present = original_read_ready
            bridge_process._is_healthy = original_is_healthy


if __name__ == "__main__":
    unittest.main()
