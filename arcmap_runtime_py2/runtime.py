# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os
import subprocess
import time
import traceback

import pythonaddins

import config_manager
import context_reader
import gateway_client
import workflow_executor


reload(config_manager)
reload(context_reader)
reload(gateway_client)
reload(workflow_executor)


try:
    unicode
except NameError:
    unicode = str


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPEN_WEB_CMD = os.path.join(REPO_ROOT, "OpenAssistantWeb.cmd")
CREATE_NO_WINDOW = 0x08000000
SILENT_COMMAND_FILE = os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "ArcMapAIAssistant",
    "bridge_command.json"
)
_LAST_COMMAND_WAS_SILENT = False
_LAST_SILENT_COMMAND = {}


def show_message(text):
    pythonaddins.MessageBox(_unicode_text(text), "ArcMap AI Assistant", 0)


def open_web():
    subprocess.Popen(
        [os.environ.get("COMSPEC", "cmd.exe"), "/c", OPEN_WEB_CMD],
        cwd=REPO_ROOT,
        creationflags=CREATE_NO_WINDOW
    )


def start_gateway():
    gateway_client.ensure_running()
    health = gateway_client.health()
    show_message(u"本地网关已启动：%s 个能力。" % health.get("operation_count"))


def ensure_gateway_silent():
    try:
        gateway_client.ensure_running()
    except (RuntimeError, OSError) as exc:
        _log_event(u"gateway.ensure_failed", _exception_text(exc))


def show_gateway_status():
    gateway_client.ensure_running()
    health = gateway_client.health()
    show_message(u"本地网关已启动：版本 %s，%s 个能力。ArcMap Bridge 由网关自动启动。" % (
        health.get("app_version"),
        health.get("operation_count")
    ))


def sync_context():
    global _LAST_COMMAND_WAS_SILENT, _LAST_SILENT_COMMAND
    _LAST_SILENT_COMMAND = _consume_silent_command("sync")
    _LAST_COMMAND_WAS_SILENT = bool(_LAST_SILENT_COMMAND)
    gateway_client.ensure_running()
    _sync_current_context()
    if not _LAST_COMMAND_WAS_SILENT:
        show_message(u"已同步当前 ArcMap 上下文。")


def handle_command(command_text):
    command_text = (command_text or "").strip()
    if not command_text:
        return

    try:
        if command_text.startswith("/key "):
            _save_key(command_text[len("/key "):])
            return True
        elif command_text == "/key":
            show_message(u"请在输入框输入：/key 你的DeepSeekKey。保存后输入框会自动清空。")
        elif command_text == "/open" or command_text == "/config":
            gateway_client.ensure_running()
            _sync_current_context()
            open_web()
            show_message(u"已打开 Web 控制台。")
        elif command_text == "/start":
            gateway_client.ensure_running()
            show_message(u"本地网关已启动。")
        elif command_text == "/health":
            gateway_client.ensure_running()
            health = gateway_client.health()
            show_message(u"网关正常：版本 %s，%s 个操作。" % (
                health.get("app_version"),
                health.get("operation_count")
            ))
        elif command_text == "/execute":
            gateway_client.ensure_running()
            _execute_pending()
        else:
            gateway_client.ensure_running()
            _plan(command_text)
    except Exception as exc:
        show_message(u"执行失败：%s" % _exception_text(exc))
        raise
    return False


def open_assistant():
    gateway_client.ensure_running()
    _sync_current_context()
    open_web()


def execute_pending():
    global _LAST_COMMAND_WAS_SILENT, _LAST_SILENT_COMMAND
    _LAST_SILENT_COMMAND = _consume_silent_command("execute")
    _LAST_COMMAND_WAS_SILENT = bool(_LAST_SILENT_COMMAND)
    gateway_client.ensure_running()
    _execute_pending(silent=_LAST_COMMAND_WAS_SILENT)


def _save_key(api_key):
    path = config_manager.save_deepseek_key(api_key)
    show_message(u"DeepSeek key 已保存到用户配置。输入 /health 检查网关。")


def _plan(command_text):
    stored = gateway_client.current_context()
    if not stored:
        raise RuntimeError(u"还没有地图上下文。请先点击“助手”或“同步”。")
    context = stored["value"]
    response = gateway_client.plan(command_text, context)
    workflow = response["workflow"]
    open_web()
    action = workflow["workflow"].get("action", "execute")
    if action == "execute":
        show_message(u"已生成工作流：%s。请在 Web 控制台审批，然后点击 ArcMap 工具栏“执行任务”。" % workflow["workflow"]["summary"])
    elif action == "clarify":
        show_message(u"需要你补充：%s" % workflow["workflow"]["summary"])
    elif action == "unsupported":
        show_message(u"当前还不支持：%s" % workflow["workflow"]["summary"])
    else:
        show_message(workflow["workflow"]["summary"])


def _execute_pending(silent=False):
    pending = gateway_client.pending()
    if not pending.get("workflow"):
        if silent:
            raise RuntimeError(u"没有已审批的工作流。")
        show_message(u"没有已审批的工作流。")
        return

    row = pending["workflow"]
    workflow_id = row["id"]
    gateway_client.claim(workflow_id)
    gateway_client.mark_executing(workflow_id)

    try:
        context = context_reader.read_context()
        result = workflow_executor.execute(row, context, confirm_callback=_confirm_direct_edit)
    except Exception as exc:
        result = {
            "ok": False,
            "error": _exception_text(exc),
            "traceback": _traceback_text()
        }
        gateway_client.execution_result(workflow_id, "failed", result)
        raise

    updated_context = _sync_current_context()
    result["context_hash"] = updated_context.get("context_hash")

    gateway_client.execution_result(workflow_id, "succeeded", result)
    if not silent:
        show_message(u"工作流执行完成：%s" % result.get("summary", "succeeded"))


def _sync_current_context():
    context = context_reader.read_context()
    gateway_client.sync_context(context)
    return context


def _consume_silent_command(action):
    try:
        if not os.path.isfile(SILENT_COMMAND_FILE):
            return {}
        with open(SILENT_COMMAND_FILE, "rb") as handle:
            raw = handle.read()
        if not isinstance(raw, unicode):
            raw = raw.decode("utf-8", "replace")
        payload = json.loads(raw.lstrip(u"\ufeff"))
        if payload.get("action") != action:
            return {}
        if float(payload.get("expires_at") or 0) < time.time():
            return {}
        try:
            os.remove(SILENT_COMMAND_FILE)
        except OSError:
            pass
        return payload if isinstance(payload, dict) else {}
    except (IOError, OSError, ValueError, TypeError) as exc:
        _log_event(u"bridge.silent_command_failed", _exception_text(exc))
        return {}


def suppress_last_error_popup():
    return bool(_LAST_COMMAND_WAS_SILENT)


def _log_event(kind, detail=None):
    try:
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ArcMapAIAssistant", "logs")
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        path = os.path.join(log_dir, "arcmap_runtime.log")
        message = u"%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), kind, _unicode_text(detail or ""))
        with open(path, "ab") as handle:
            handle.write(message.encode("utf-8", "replace"))
    except (IOError, OSError):
        pass


def _exception_text(exc):
    if getattr(exc, "args", None):
        parts = [_unicode_text(arg) for arg in exc.args]
        return u" ".join([part for part in parts if part]) or _unicode_text(exc.__class__.__name__)
    return _unicode_text(exc)


def _traceback_text():
    try:
        return _unicode_text(traceback.format_exc())
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError):
        return u""


def _unicode_text(value):
    try:
        unicode
    except NameError:
        return str(value)

    if isinstance(value, unicode):
        return value
    if isinstance(value, str):
        return value.decode("utf-8", "replace")
    try:
        return unicode(value)
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError):
        try:
            return str(value).decode("utf-8", "replace")
        except (UnicodeDecodeError, UnicodeEncodeError, TypeError, AttributeError):
            return u"<unprintable>"


def _confirm_direct_edit(message):
    if _LAST_COMMAND_WAS_SILENT:
        return bool(_LAST_SILENT_COMMAND.get("allow_edits"))
    text = _unicode_text(message) + u"\n\n这会直接修改原始数据，且不承诺可撤销。是否继续？"
    result = pythonaddins.MessageBox(text, "ArcMap AI Assistant", 4)
    if isinstance(result, bool):
        return result
    value = _unicode_text(result).lower()
    return value in (u"yes", u"y", u"true", u"1", u"6", u"是", u"确定")
