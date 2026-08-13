# -*- coding: utf-8 -*-
"""Owner-bound synchronous UI-thread dispatch for ArcMap silent commands.

Architecture
------------
The ArcMap Add-in's ``OpenAssistantButton.onClick`` is the *only* legitimate
entry point on the ArcMap UI thread.  It loads ``runtime`` and synchronously
calls ``runtime.open_or_handle_bridge_command()``.  Every Bridge silent command
is therefore consumed on that UI thread, and the ArcPy calls inside the
deferred callback must run there too -- they touch the MxDocument, which only
the UI thread may safely access.

This module deliberately does **not** marshal callbacks across threads.  There
is no ``SetTimer``, no message pump, no worker queue, no retry and no sleep.
Instead the Add-in UI thread registers itself once as the *owner* via
``register_ui_owner()`` (re-exported by ``runtime.bind_ui_thread``), and
``defer()`` only ever executes a callback when the calling thread **is** that
owner.  A call from any other (worker) thread fails closed immediately with a
typed ``WrongUIOwnerError`` -- it never runs the callback on the worker and
never leaves a pending callback behind to block later executions.

The retired ``SetTimer(NULL, ...)`` design posted the timer to the *calling*
thread's message queue; on a worker thread that queue has no pump, so the timer
never fired and a stale ``_PENDING_CALLBACK`` permanently blocked every later
execution.  The synchronous-then-owner-bound model removes that entire class of
defect: there is never any pending state to go stale, and the wrong thread is
told "no" instead of silently running ArcPy where it cannot.
"""

try:  # Python 2
    import thread as _thread
except ImportError:  # Python 3
    import _thread

_get_ident = _thread.get_ident


class WrongUIOwnerError(RuntimeError):
    """``defer()`` was invoked off the registered ArcMap UI owner thread.

    The Add-in UI thread is the only thread permitted to run deferred ArcPy
    work.  A worker thread that reaches ``defer()`` means the Bridge routed the
    command to the wrong thread; failing closed here is safer than silently
    running ArcPy on a thread that cannot safely touch the MxDocument.  This is
    a subclass of ``RuntimeError`` so callers catching ``RuntimeError`` still
    see it, while tests can assert on the typed exception.
    """


# Identity of the thread that registered itself as the ArcMap UI owner, or
# ``None`` before registration.  Bound at most once per process; rebinding from
# a different thread is rejected so the ownership boundary stays unambiguous.
_ui_owner_ident = None

# Re-entrancy guard: ``True`` while a deferred callback is executing.  Cleared
# in ``finally`` so a crashed callback never permanently blocks the next one.
_EXECUTING = False


def register_ui_owner():
    """Bind the calling thread as the ArcMap UI owner.

    Idempotent for the same thread: calling it repeatedly on the already-bound
    owner thread is a no-op (the Add-in calls it before every command).  A call
    from a *different* thread is rejected with ``RuntimeError`` so the UI
    ownership boundary can never be silently hijacked.
    """
    global _ui_owner_ident
    ident = _get_ident()
    if _ui_owner_ident is None:
        _ui_owner_ident = ident
        return
    if _ui_owner_ident != ident:
        raise RuntimeError(
            "UI owner already bound to thread %r; refusing to rebind to %r."
            % (_ui_owner_ident, ident)
        )


def ui_owner_ident():
    """Return the thread ident of the registered UI owner, or ``None``."""
    return _ui_owner_ident


def current_ui_owner():
    """Alias kept as an explicit, queryable UI-owner handle."""
    return _ui_owner_ident


def defer(callback):
    """Execute ``callback`` synchronously, but only on the UI owner thread.

    Contract:

    * The calling thread **must** be the registered UI owner (bound via
      ``register_ui_owner``).  Any other thread fails closed immediately with
      ``WrongUIOwnerError`` -- the callback is never executed on the worker and
      no pending state is left behind (``_EXECUTING`` never becomes ``True``).
    * Re-entrant calls while an execution is already in progress fail fast with
      ``RuntimeError``.
    * If the callback itself raises, the re-entrancy guard is cleared in
      ``finally`` and the exception propagates, so a later ``defer()`` still
      works.
    """
    global _EXECUTING
    if not callable(callback):
        raise TypeError("ArcMap UI callback must be callable.")
    _assert_ui_owner()
    if _EXECUTING:
        raise RuntimeError("an execution is already in progress on this thread")
    _EXECUTING = True
    try:
        callback()
    finally:
        _EXECUTING = False


def _assert_ui_owner():
    if _ui_owner_ident is None:
        raise WrongUIOwnerError(
            "no ArcMap UI owner thread has been registered; call "
            "register_ui_owner() from the Add-in UI thread first."
        )
    if _ui_owner_ident != _get_ident():
        raise WrongUIOwnerError(
            "defer() was invoked off the ArcMap UI owner thread "
            "(caller=%r, owner=%r); refusing to execute ArcPy on a worker "
            "thread." % (_get_ident(), _ui_owner_ident)
        )


def is_executing():
    """Return True if a deferred execution is currently in progress."""
    return _EXECUTING
