# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import subprocess
import time

try:
    import urllib2
except ImportError:
    import urllib.request as urllib2

try:
    import path_utils
    from shared_runtime import platform_paths
except ImportError:
    from . import path_utils
    from shared_runtime import platform_paths


CREATE_NO_WINDOW = 0x08000000
START_TIMEOUT_SECONDS = 30.0
POLL_INTERVAL_SECONDS = 0.1
READY_FILE = platform_paths.localappdata_path("bridge.ready")


def ensure_running(bridge_exe):
    """Return the live Bridge identity, starting the installed Bridge if needed."""
    if not path_utils.isfile(bridge_exe):
        raise RuntimeError(u"ArcMap Bridge executable is missing: %s" % bridge_exe)

    ready = _read_ready_if_present()
    if ready is not None and _is_healthy(ready):
        return ready

    if path_utils.isfile(READY_FILE):
        path_utils.remove(READY_FILE)

    process = subprocess.Popen(
        [bridge_exe],
        cwd=path_utils.dirname(bridge_exe),
        creationflags=CREATE_NO_WINDOW,
    )
    deadline = time.time() + START_TIMEOUT_SECONDS
    try:
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError(u"ArcMap Bridge exited during startup with code %s." % process.returncode)
            ready = _read_ready_if_present()
            if ready is not None:
                if int(ready.get("pid") or 0) != process.pid:
                    raise RuntimeError(u"ArcMap Bridge ready-file belongs to another process.")
                if _is_healthy(ready):
                    return ready
            time.sleep(POLL_INTERVAL_SECONDS)
    except Exception:
        if process.poll() is None:
            process.terminate()
        raise
    if process.poll() is None:
        process.terminate()
    raise RuntimeError(u"ArcMap Bridge did not become healthy within %.1f seconds." % START_TIMEOUT_SECONDS)


def _read_ready_if_present():
    if not path_utils.isfile(READY_FILE):
        return None
    with path_utils.open_binary(READY_FILE, "rb") as handle:
        raw = handle.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise RuntimeError(u"ArcMap Bridge ready-file is not strict UTF-8 without BOM.")
    text = raw.decode("utf-8")
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise RuntimeError(u"ArcMap Bridge ready-file must be a JSON object.")
    port = int(payload.get("port") or 0)
    pid = int(payload.get("pid") or 0)
    if port <= 0 or pid <= 0:
        raise RuntimeError(u"ArcMap Bridge ready-file contains an invalid process identity.")
    return payload


def _is_healthy(ready):
    port = int(ready["port"])
    try:
        response = urllib2.urlopen("http://127.0.0.1:%d/health" % port, timeout=2.0)
        try:
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            response.close()
    except (IOError, OSError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("ok") is True
        and int(payload.get("bridge_pid") or 0) == int(ready["pid"])
    )
