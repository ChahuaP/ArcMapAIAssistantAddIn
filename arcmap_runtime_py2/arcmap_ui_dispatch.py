# -*- coding: utf-8 -*-
from __future__ import absolute_import

import ctypes
from ctypes import wintypes


_UINT_PTR = ctypes.c_size_t
_TIMER_CALLBACK = ctypes.WINFUNCTYPE(
    None,
    wintypes.HWND,
    wintypes.UINT,
    _UINT_PTR,
    wintypes.DWORD,
)
_USER32 = ctypes.windll.user32
_USER32.SetTimer.argtypes = (
    wintypes.HWND,
    _UINT_PTR,
    wintypes.UINT,
    _TIMER_CALLBACK,
)
_USER32.SetTimer.restype = _UINT_PTR
_USER32.KillTimer.argtypes = (wintypes.HWND, _UINT_PTR)
_USER32.KillTimer.restype = wintypes.BOOL

_PENDING_CALLBACK = None
_TIMER_ID = 0
_TIMER_PROC = None


def defer(callback):
    """Run callback after the current ArcMap UI callback returns to its message loop."""
    global _PENDING_CALLBACK, _TIMER_ID, _TIMER_PROC
    if not callable(callback):
        raise TypeError("ArcMap UI callback must be callable.")
    if _PENDING_CALLBACK is not None:
        raise RuntimeError("ArcMap UI already has a deferred execution pending.")

    timer_proc = _TIMER_CALLBACK(_dispatch)
    _PENDING_CALLBACK = callback
    _TIMER_PROC = timer_proc
    timer_id = _set_timer(timer_proc)
    if not timer_id:
        _clear_pending()
        raise ctypes.WinError()
    _TIMER_ID = timer_id
    return timer_id


def _dispatch(hwnd, message, timer_id, tick_count):
    del hwnd, message, tick_count
    if timer_id != _TIMER_ID:
        return
    callback = _PENDING_CALLBACK
    if not _kill_timer(timer_id):
        _clear_pending()
        raise ctypes.WinError()
    _clear_pending()
    callback()


def _set_timer(timer_proc):
    return _USER32.SetTimer(None, 0, 1, timer_proc)


def _kill_timer(timer_id):
    return bool(_USER32.KillTimer(None, timer_id))


def _clear_pending():
    global _PENDING_CALLBACK, _TIMER_ID, _TIMER_PROC
    _PENDING_CALLBACK = None
    _TIMER_ID = 0
    _TIMER_PROC = None
