from __future__ import annotations

import os
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from gateway_py3.paths import REPO_ROOT, localappdata_dir


BASE_URL = "http://127.0.0.1:8765"
EXPECTED_APP_VERSION = "0.21.2"
WEB_URL = BASE_URL
CREATE_NO_WINDOW = 0x08000000


def main() -> None:
    ensure_gateway()
    os.startfile(WEB_URL)


def ensure_gateway() -> None:
    health = health_payload()
    if is_expected_version(health):
        return
    if health:
        stop_gateway()
    start_gateway()
    deadline = time.time() + 15
    while time.time() < deadline:
        if is_expected_version(health_payload()):
            return
        time.sleep(0.4)
    raise RuntimeError("ArcMap AI Assistant Gateway did not start.")


def health_payload() -> dict | None:
    try:
        with urllib.request.urlopen(BASE_URL + "/health", timeout=2) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError):
        return None


def is_expected_version(payload: dict | None) -> bool:
    return bool(payload and payload.get("app_version") == EXPECTED_APP_VERSION)


def stop_gateway() -> None:
    if os.name != "nt":
        return
    try:
        output = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            creationflags=CREATE_NO_WINDOW
        ).decode("mbcs", "replace")
    except (subprocess.CalledProcessError, OSError):
        return
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(":8765") and parts[3].upper() == "LISTENING":
            pid = parts[4]
            if pid.isdigit() and int(pid) != os.getpid():
                subprocess.call(["taskkill", "/PID", pid, "/F"], creationflags=CREATE_NO_WINDOW)
            return


def start_gateway() -> None:
    log_dir = localappdata_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = open(log_dir / "gateway_stdout.log", "ab")
    stderr = open(log_dir / "gateway_stderr.log", "ab")
    subprocess.Popen(
        [_python_executable(), "-m", "gateway_py3"],
        cwd=str(REPO_ROOT),
        stdout=stdout,
        stderr=stderr,
        creationflags=CREATE_NO_WINDOW
    )


def _python_executable() -> str:
    executable = Path(sys.executable)
    pythonw = executable.with_name("pythonw.exe")
    if pythonw.exists():
        return str(pythonw)
    return str(executable)


if __name__ == "__main__":
    main()
