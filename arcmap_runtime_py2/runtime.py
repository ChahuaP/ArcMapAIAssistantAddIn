# -*- coding: utf-8 -*-
from __future__ import absolute_import

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


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OPEN_WEB_CMD = os.path.join(REPO_ROOT, "OpenAssistantWeb.cmd")
CREATE_NO_WINDOW = 0x08000000
AUTO_SYNC_MIN_INTERVAL_SECONDS = 2.0
AUTO_SYNC_GATEWAY_CHECK_INTERVAL_SECONDS = 10.0
AUTO_SYNC_GATEWAY_TIMEOUT_SECONDS = 0.25

_last_auto_sync_at = 0.0
_last_auto_sync_hash = None
_last_gateway_check_at = 0.0
_last_gateway_available = False


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
    _sync_context(force=True, start_gateway=False, show_confirmation=False)
    health = gateway_client.health()
    show_message(u"本地网关已启动：%s 个能力。" % health.get("operation_count"))


def sync_context():
    _sync_context(force=True, start_gateway=True, show_confirmation=False)
    show_message(u"已同步当前 ArcMap 上下文。")


def auto_sync_context(event_name=None):
    try:
        return _sync_context(force=False, start_gateway=False, show_confirmation=False, event_name=event_name)
    except Exception:
        _log_event(u"auto_sync.failed", event_name)
        return False


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
            open_web()
            show_message(u"已打开 Web 控制台。")
        elif command_text == "/start":
            gateway_client.ensure_running()
            show_message(u"本地网关已启动。")
        elif command_text == "/health":
            gateway_client.ensure_running()
            health = gateway_client.health()
            show_message(u"网关正常：%s 个操作，catalog %s。" % (health.get("operation_count"), health.get("catalog_version")))
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
    _sync_context(force=True, start_gateway=False, show_confirmation=False)
    open_web()


def execute_pending():
    gateway_client.ensure_running()
    _execute_pending()


def _save_key(api_key):
    path = config_manager.save_deepseek_key(api_key)
    show_message(u"DeepSeek key 已保存到用户配置。输入 /health 检查网关。")


def _plan(command_text):
    context = context_reader.read_context()
    gateway_client.sync_context(context)
    _remember_synced_context(context)
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


def _execute_pending():
    pending = gateway_client.pending()
    if not pending.get("workflow"):
        show_message(u"没有已审批的工作流。")
        return

    row = pending["workflow"]
    workflow_id = row["id"]
    gateway_client.claim(workflow_id)
    gateway_client.mark_executing(workflow_id)

    try:
        context = context_reader.read_context()
        result = workflow_executor.execute(row, context)
    except Exception as exc:
        result = {
            "ok": False,
            "error": _exception_text(exc),
            "traceback": _traceback_text()
        }
        gateway_client.execution_result(workflow_id, "failed", result)
        raise

    gateway_client.execution_result(workflow_id, "succeeded", result)
    show_message(u"工作流执行完成：%s" % result.get("summary", "succeeded"))


def _sync_context(force, start_gateway, show_confirmation, event_name=None):
    if start_gateway:
        gateway_client.ensure_running()
    elif not _gateway_available_for_auto_sync():
        return False

    now = time.time()
    if not force and now - _last_auto_sync_at < AUTO_SYNC_MIN_INTERVAL_SECONDS:
        return False

    context = context_reader.read_context()
    context_hash = context.get("context_hash")
    if not force and context_hash and context_hash == _last_auto_sync_hash:
        return False

    gateway_client.sync_context(context)
    _remember_synced_context(context)
    if show_confirmation:
        show_message(u"已同步当前 ArcMap 上下文。")
    return True


def _gateway_available_for_auto_sync():
    global _last_gateway_check_at
    global _last_gateway_available

    now = time.time()
    if now - _last_gateway_check_at < AUTO_SYNC_GATEWAY_CHECK_INTERVAL_SECONDS:
        return _last_gateway_available

    _last_gateway_check_at = now
    _last_gateway_available = gateway_client.is_compatible(timeout=AUTO_SYNC_GATEWAY_TIMEOUT_SECONDS)
    return _last_gateway_available


def _remember_synced_context(context):
    global _last_auto_sync_at
    global _last_auto_sync_hash
    global _last_gateway_check_at
    global _last_gateway_available

    _last_auto_sync_at = time.time()
    _last_auto_sync_hash = context.get("context_hash")
    _last_gateway_check_at = _last_auto_sync_at
    _last_gateway_available = True


def _log_event(kind, detail=None):
    try:
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ArcMapAIAssistant", "logs")
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        path = os.path.join(log_dir, "arcmap_runtime.log")
        message = u"%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), kind, _unicode_text(detail or ""))
        with open(path, "ab") as handle:
            handle.write(message.encode("utf-8", "replace"))
    except Exception:
        pass


def _exception_text(exc):
    if getattr(exc, "args", None):
        parts = [_unicode_text(arg) for arg in exc.args]
        return u" ".join([part for part in parts if part]) or _unicode_text(exc.__class__.__name__)
    return _unicode_text(exc)


def _traceback_text():
    try:
        return _unicode_text(traceback.format_exc())
    except Exception:
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
    except Exception:
        try:
            return str(value).decode("utf-8", "replace")
        except Exception:
            return u"<unprintable>"
