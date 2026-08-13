# -*- coding: utf-8 -*-
"""Regression tests for :mod:`arcmap_runtime_py2.arcmap_ui_dispatch`.

These tests pin the **owner-bound, fail-closed** threading contract of the
ArcMap dispatch layer.

Architecture under test
-----------------------
The ArcMap Add-in's ``onClick`` is the only ArcMap UI-thread entry point.  It
synchronously calls ``runtime.open_or_handle_bridge_command()``, so every
Bridge silent command is consumed on that UI thread.  The dispatch layer must
*not* marshal callbacks across threads in Python: the Add-in UI thread
registers itself once via ``register_ui_owner()`` (re-exported as
``runtime.bind_ui_thread``), and ``defer()`` only runs a callback when the
caller **is** that owner.  Any other (worker) thread must fail closed
immediately with a typed ``WrongUIOwnerError(RuntimeError)`` -- the callback is
never executed on the worker and no pending state is left behind.

This contract removes the root defect of the retired ``SetTimer(NULL, ...)``
design: that design posted the timer to the *calling* thread's queue, so on a
worker thread (the common Bridge path) the timer never fired and a stale
``_PENDING_CALLBACK`` permanently blocked every later execution.  The earlier
"synchronous" replacement ran the callback inline on whatever thread called
``defer()`` -- which silently executed ArcPy on the wrong thread.  The
owner-bound model is the single correct architecture: the Add-in UI thread is
the explicit owner, and a non-owner is told "no" instead of running ArcPy where
it cannot.

Contract pinned here:

* the main/UI thread calls ``register_ui_owner()`` first;
* an owner calling ``defer()`` runs the callback synchronously on the owner;
* a worker calling ``defer()`` raises ``WrongUIOwnerError`` immediately, never
  executes the callback, and leaves no pending guard behind;
* re-entrant ``defer()`` fast-fails;
* a callback that raises still clears the guard (``finally``);
* owner binding is idempotent for the same thread and rejects a different
  thread;
* ``defer()`` before any owner is registered fails closed.

Tests are Py2-compatible and free of any ``arcpy`` import.
"""
from __future__ import absolute_import

import os
import sys
import threading
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# Low-level thread identity is reliable on both CPython 2.7 and 3.x; the
# ``Thread.ident`` attribute is not set for the main thread on 2.7, so prefer
# ``thread.get_ident`` / ``_thread.get_ident`` directly.
try:  # Python 2
    import thread as _thread
except ImportError:  # Python 3
    import _thread

_get_ident = _thread.get_ident

PY2 = sys.version_info[0] == 2
if PY2:
    from arcmap_runtime_py2 import arcmap_ui_dispatch


@unittest.skipUnless(PY2, "ArcMap Python 2.7 runtime test")
class OwnerBoundDeferContractTests(unittest.TestCase):
    """``defer()`` is owner-bound and fails closed off the UI owner thread."""

    def setUp(self):
        # The owner identity and re-entrancy guard are module-global; reset
        # both so state from a previously aborted test cannot poison this one.
        arcmap_ui_dispatch._ui_owner_ident = None
        arcmap_ui_dispatch._EXECUTING = False

    # -- happy path -------------------------------------------------------

    def test_owner_defer_runs_callback_synchronously_on_owner(self):
        """An owner-thread ``defer()`` runs the callback on the owner thread.

        The main thread stands in for the ArcMap UI thread: it registers as the
        owner, then calls ``defer()`` directly.  The callback must execute on
        this same owner thread, before ``defer()`` returns (synchronous).
        """
        arcmap_ui_dispatch.register_ui_owner()
        owner_ident = arcmap_ui_dispatch.ui_owner_ident()
        self.assertEqual(owner_ident, _get_ident())

        recorded = {}
        order = []
        arcmap_ui_dispatch.defer(lambda: (
            recorded.setdefault("thread", _get_ident()),
            order.append("ran"),
        ))
        # Synchronous: the callback ran before defer returned.
        self.assertEqual(order, ["ran"])
        self.assertEqual(recorded.get("thread"), owner_ident)

    def test_exposes_an_explicit_ui_owner_handle(self):
        """The dispatch layer carries an explicit, queryable UI owner handle."""
        self.assertTrue(
            hasattr(arcmap_ui_dispatch, "ui_owner_ident")
            or hasattr(arcmap_ui_dispatch, "current_ui_owner"),
            "arcmap_ui_dispatch exposes no explicit UI owner handle.",
        )
        self.assertIsNone(arcmap_ui_dispatch.ui_owner_ident())
        arcmap_ui_dispatch.register_ui_owner()
        self.assertIsNotNone(arcmap_ui_dispatch.ui_owner_ident())

    # -- fail closed off the UI owner thread ------------------------------

    def test_worker_defer_fails_closed_with_typed_error_and_leaves_no_pending(self):
        """The SetTimer(NULL, ...) defect class: a worker must not run ArcPy.

        The owner is the main thread.  A dedicated worker thread then calls
        ``defer()``.  The dispatch layer must raise a typed
        ``WrongUIOwnerError`` (a ``RuntimeError``) immediately, must NOT
        execute the callback on the worker, and must NOT leave the re-entrancy
        guard pending (so a later owner execution is not blocked -- the exact
        way the old stale ``_PENDING_CALLBACK`` permanently jammed dispatch).
        """
        arcmap_ui_dispatch.register_ui_owner()
        owner_ident = arcmap_ui_dispatch.ui_owner_ident()

        ran = {}
        worker_outcome = {}

        def callback():
            ran["thread"] = _get_ident()

        def worker():
            try:
                arcmap_ui_dispatch.defer(callback)
            except Exception as exc:
                worker_outcome["exc"] = exc

        worker_thread = threading.Thread(target=worker, name="bridge-worker")
        worker_thread.start()
        worker_thread.join()

        # Typed error raised on the worker thread.
        self.assertIsInstance(
            worker_outcome.get("exc"),
            arcmap_ui_dispatch.WrongUIOwnerError,
            "defer() on a worker must raise WrongUIOwnerError, got %r"
            % (worker_outcome.get("exc"),),
        )
        self.assertIsInstance(
            worker_outcome.get("exc"), RuntimeError,
            "WrongUIOwnerError must be a RuntimeError.",
        )
        # The callback was never executed on the worker.
        self.assertNotIn(
            "thread", ran,
            "defer() must not execute the callback on a worker thread.",
        )
        # No pending guard left behind to block a later owner execution.
        self.assertFalse(
            arcmap_ui_dispatch.is_executing(),
            "a failed worker defer() must not leave the guard pending.",
        )

        # And the owner can still dispatch immediately afterwards -- this is
        # the regression for the stale-pending-permanent-block defect.
        after = []
        arcmap_ui_dispatch.defer(lambda: after.append(owner_ident))
        self.assertEqual(after, [owner_ident])

    def test_defer_before_owner_registered_fails_closed(self):
        """``defer()`` with no registered owner fails closed, never executes."""
        ran = []
        with self.assertRaises(arcmap_ui_dispatch.WrongUIOwnerError):
            arcmap_ui_dispatch.defer(lambda: ran.append(True))
        self.assertEqual(ran, [])
        self.assertFalse(arcmap_ui_dispatch.is_executing())

    # -- re-entrancy lifecycle -------------------------------------------

    def test_reentrant_defer_fast_fails(self):
        """A ``defer()`` issued while one is already executing fast-fails."""
        arcmap_ui_dispatch.register_ui_owner()
        nested = {}

        def outer_callback():
            try:
                arcmap_ui_dispatch.defer(lambda: None)
            except Exception as exc:
                nested["exc"] = exc

        arcmap_ui_dispatch.defer(outer_callback)
        self.assertIsInstance(nested.get("exc"), RuntimeError)
        # The guard was still cleared after the outer execution.
        self.assertFalse(arcmap_ui_dispatch.is_executing())

    def test_callback_exception_clears_guard(self):
        """A crashing callback must not leave the guard permanently set."""
        arcmap_ui_dispatch.register_ui_owner()

        class Boom(Exception):
            pass

        def crash():
            raise Boom("callback exploded")

        with self.assertRaises(Boom):
            arcmap_ui_dispatch.defer(crash)

        self.assertFalse(
            arcmap_ui_dispatch.is_executing(),
            "guard must be cleared in finally after a callback exception.",
        )
        # A subsequent owner execution must still work.
        ran = []
        arcmap_ui_dispatch.defer(lambda: ran.append(True))
        self.assertEqual(ran, [True])

    # -- owner binding lifecycle -----------------------------------------

    def test_register_ui_owner_is_idempotent_on_same_thread(self):
        """Repeated binding on the already-bound owner thread is a no-op."""
        arcmap_ui_dispatch.register_ui_owner()
        first = arcmap_ui_dispatch.ui_owner_ident()
        # Idempotent: must not raise, must not change the bound identity.
        arcmap_ui_dispatch.register_ui_owner()
        arcmap_ui_dispatch.register_ui_owner()
        self.assertEqual(arcmap_ui_dispatch.ui_owner_ident(), first)

    def test_register_ui_owner_rejects_a_different_thread(self):
        """A different thread must not be able to hijack the UI ownership."""
        arcmap_ui_dispatch.register_ui_owner()
        owner_before = arcmap_ui_dispatch.ui_owner_ident()

        rebinding = {}

        def worker():
            try:
                arcmap_ui_dispatch.register_ui_owner()
            except Exception as exc:
                rebinding["exc"] = exc

        worker_thread = threading.Thread(target=worker, name="hijacker")
        worker_thread.start()
        worker_thread.join()

        self.assertIsInstance(rebinding.get("exc"), RuntimeError)
        # Ownership unchanged -- still the main (UI) thread.
        self.assertEqual(arcmap_ui_dispatch.ui_owner_ident(), owner_before)


if __name__ == "__main__":
    unittest.main()
