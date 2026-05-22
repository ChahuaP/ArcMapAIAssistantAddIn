from __future__ import annotations

import base64
import subprocess


class FolderDialogError(Exception):
    pass


def select_folder(title: str = "选择 GeoPilot 项目工作目录") -> dict:
    command = _powershell_command(title)
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired:
        raise FolderDialogError("选择文件夹超时，请重试。")
    except OSError as exc:
        raise FolderDialogError("无法打开系统文件夹选择器：%s" % exc)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise FolderDialogError("系统文件夹选择器启动失败：%s" % (detail or completed.returncode))
    path = (completed.stdout or "").strip()
    if not path:
        return {"ok": False, "cancelled": True, "path": ""}
    return {"ok": True, "cancelled": False, "path": path}


def _powershell_command(title: str) -> list[str]:
    return [
        "powershell.exe",
        "-NoLogo",
        "-NoProfile",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-EncodedCommand",
        _encoded_script(title),
    ]


def _encoded_script(title: str) -> str:
    return base64.b64encode(_folder_dialog_script(title).encode("utf-16le")).decode("ascii")


def _folder_dialog_script(title: str) -> str:
    safe_title = title.replace("'", "''")
    return """
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $false
$owner.StartPosition = 'CenterScreen'
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '%s'
$dialog.ShowNewFolderButton = $true
try {
  $result = $dialog.ShowDialog($owner)
  if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine($dialog.SelectedPath)
  }
}
finally {
  $dialog.Dispose()
  $owner.Dispose()
}
""" % safe_title
