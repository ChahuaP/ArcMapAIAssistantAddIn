# -*- coding: utf-8 -*-
from __future__ import absolute_import

import os
import sys
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from arcmap_runtime_py2 import arcmap_ui_dispatch


class ArcMapUiDispatchPython27Tests(unittest.TestCase):
    def tearDown(self):
        arcmap_ui_dispatch._clear_pending()

    def test_callback_is_deferred_and_released_after_one_timer_message(self):
        events = []
        timer_procs = []
        original_set_timer = arcmap_ui_dispatch._set_timer
        original_kill_timer = arcmap_ui_dispatch._kill_timer
        try:
            arcmap_ui_dispatch._set_timer = lambda timer_proc: timer_procs.append(timer_proc) or 53
            arcmap_ui_dispatch._kill_timer = lambda timer_id: events.append(("kill", timer_id)) or True

            arcmap_ui_dispatch.defer(lambda: events.append("run"))
            self.assertEqual(events, [])
            with self.assertRaises(RuntimeError):
                arcmap_ui_dispatch.defer(lambda: None)

            timer_procs[0](None, 0x0113, 53, 0)

            self.assertEqual(events, [("kill", 53), "run"])
        finally:
            arcmap_ui_dispatch._set_timer = original_set_timer
            arcmap_ui_dispatch._kill_timer = original_kill_timer


if __name__ == "__main__":
    unittest.main()
