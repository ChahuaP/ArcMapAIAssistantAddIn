# -*- coding: utf-8 -*-
from __future__ import absolute_import

import json
import os
import subprocess
import threading
import time
import traceback
import uuid

import pythonaddins

try:
    import arcmap_ui_dispatch
    import context_reader
    import execution_session
    import execution_outbox
    import exception_text
    import gateway_client
    import map_exporter
    import output_publisher
    import path_utils
    import workflow_executor
except ImportError:
    from . import arcmap_ui_dispatch
    from . import context_reader
    from . import execution_session
    from . import execution_outbox
    from . import exception_text
    from . import gateway_client
    from . import map_exporter
    from . import output_publisher
    from . import path_utils
    from . import workflow_executor


try:
    unicode
except NameError:
    unicode = str


REPO_ROOT = path_utils.abspath(path_utils.join_path(os.path.dirname(__file__), ".."))
OPEN_WEB_CMD = path_utils.join_path(REPO_ROOT, "OpenAssistantWeb.cmd")
CREATE_NO_WINDOW = 0x08000000
SILENT_COMMAND_FILE = path_utils.join_path(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "ArcMapAIAssistant",
    "bridge_command.json"
)
_LAST_COMMAND_WAS_SILENT = False
_LAST_SILENT_COMMAND = {}
_DELIVERY_WORKERS = {}
_DELIVERY_LOCK = threading.Lock()
EXECUTION_OUTBOX = execution_outbox.ExecutionOutbox(path_utils.join_path(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
    "ArcMapAIAssistant",
    "execution_outbox",
))


def show_message(text):
    pythonaddins.MessageBox(_unicode_text(text), "ArcMap AI Assistant", 0)


def open_web():
    subprocess.Popen(
        [os.environ.get("COMSPEC", "cmd.exe"), "/c", OPEN_WEB_CMD],
        cwd=REPO_ROOT,
        creationflags=CREATE_NO_WINDOW
    )


def open_or_handle_bridge_command():
    command = _consume_silent_command()
    if command:
        _run_silent_command(command)
        return
    open_assistant()


def open_assistant():
    _clear_silent_state()
    gateway_client.ensure_running()
    map_exporter.cleanup_stale()
    _sync_current_context()
    open_web()


def _run_silent_command(command):
    global _LAST_COMMAND_WAS_SILENT, _LAST_SILENT_COMMAND
    _LAST_SILENT_COMMAND = command
    _LAST_COMMAND_WAS_SILENT = True
    gateway_client.ensure_running()
    map_exporter.cleanup_stale()
    _drain_execution_outbox(command.get("target"))
    action = command.get("action")
    if action == "sync":
        _sync_current_context(
            command.get("run_id"),
            command.get("sync_token"),
            command.get("phase"),
            command.get("target"),
        )
        return
    if action == "execute":
        run_id = command.get("run_id")
        target = command.get("target")
        owner_id = command.get("owner_id")
        row = _claim_run(run_id, target, owner_id)
        heartbeat = _start_execution_heartbeat(run_id, owner_id)
        try:
            arcmap_ui_dispatch.defer(lambda: _run_deferred_execution(
                run_id, target, owner_id, row, heartbeat,
            ))
        except Exception as exc:
            _persist_claimed_failure(
                run_id, owner_id, target, heartbeat, exc, u"arcmap_ui_dispatch",
            )
            raise
        _log_event(u"execution.deferred_to_arcmap_ui", run_id)
        return
    raise RuntimeError(u"未知 Bridge 指令：%s" % _unicode_text(action))


def _claim_run(run_id, target, owner_id):
    _validate_execution_identity(run_id, owner_id)
    return gateway_client.claim_run(run_id, target, owner_id)["run"]


def _validate_execution_identity(run_id, owner_id):
    if not isinstance(run_id, unicode) or not run_id:
        raise RuntimeError(u"Bridge execute command lacks run_id.")
    if not isinstance(owner_id, unicode) or not owner_id:
        raise RuntimeError(u"Bridge execute command lacks owner_id.")
    try:
        parsed_run = unicode(uuid.UUID(run_id))
        parsed_owner = unicode(uuid.UUID(owner_id))
    except (ValueError, AttributeError, TypeError):
        raise RuntimeError(u"Bridge execute command identity is invalid.")
    if parsed_run != run_id:
        raise RuntimeError(u"Bridge execute command run_id is not canonical.")
    if parsed_owner != owner_id:
        raise RuntimeError(u"Bridge execute command owner_id is not canonical.")


def _start_execution_heartbeat(run_id, owner_id):
    heartbeat = _ExecutionHeartbeat(run_id, owner_id)
    heartbeat.start()
    return heartbeat


def _run_deferred_execution(run_id, target, owner_id, row, heartbeat):
    try:
        _execute_claimed_run(
            run_id, target, owner_id, row, heartbeat, silent=True,
        )
    except Exception as exc:
        _log_event(u"execution.deferred_failed", _exception_text(exc))


def _execute_run(run_id, target, owner_id, silent=False):
    row = _claim_run(run_id, target, owner_id)
    heartbeat = _start_execution_heartbeat(run_id, owner_id)
    return _execute_claimed_run(
        run_id, target, owner_id, row, heartbeat, silent=silent,
    )


def _execute_claimed_run(run_id, target, owner_id, row, heartbeat, silent=False):
    try:
        context = context_reader.read_context()
        outcome = workflow_executor.execute(row, context, confirm_callback=_confirm_direct_edit)
        result = outcome.result
    except Exception as exc:
        result = {
            "ok": False,
            "error": _exception_text(exc),
            "traceback": _traceback_text(),
            "postcondition_failure": _postcondition_failure(exc),
        }
        _persist_publish_and_deliver(
            run_id, owner_id, "failed", result, target, heartbeat,
            execution_session.PublicationPlan([]),
        )
        raise

    acknowledged = _persist_publish_and_deliver(
        run_id, owner_id, "executed", result, target, heartbeat, outcome.publication_plan,
    )
    if not silent:
        if acknowledged:
            show_message(u"工作流执行完成：%s" % result.get("summary", "succeeded"))
        else:
            show_message(u"工作流已执行完成，权威结果正在重试提交到本地网关。")


def _persist_claimed_failure(run_id, owner_id, target, heartbeat, exc, phase):
    result = {
        "ok": False,
        "error": _exception_text(exc),
        "traceback": _traceback_text(),
        "postcondition_failure": None,
        "failure_phase": phase,
    }
    _persist_publish_and_deliver(
        run_id, owner_id, "failed", result, target, heartbeat,
        execution_session.PublicationPlan([]),
    )


def _persist_publish_and_deliver(run_id, owner_id, status, result, target, heartbeat, publication_plan):
    try:
        entry = EXECUTION_OUTBOX.enqueue(
            run_id, owner_id, status, result, target, publication_plan.records,
        )
    except Exception as exc:
        heartbeat.stop()
        _log_event(u"execution.outbox_persist_failed", _exception_text(exc))
        raise
    if not entry["publication_complete"]:
        try:
            with EXECUTION_OUTBOX.publication_lease(entry) as acquired:
                if not acquired:
                    raise RuntimeError("execution output publication is already active.")
                output_publisher.publish(publication_plan)
                _mark_publication_observed(result, publication_plan)
                if hasattr(EXECUTION_OUTBOX, "replace_result"):
                    entry = EXECUTION_OUTBOX.replace_result(entry, result)
                entry = EXECUTION_OUTBOX.mark_publication_complete(entry)
        except Exception as exc:
            heartbeat.stop()
            _log_event(u"execution.output_publication_failed", _exception_text(exc))
            raise
    try:
        acknowledged = EXECUTION_OUTBOX.deliver(entry, gateway_client)
    except Exception as exc:
        _log_event(u"execution.delivery_failed", _exception_text(exc))
        _start_delivery_retry(entry, heartbeat)
        return False
    if not acknowledged:
        _start_delivery_retry(entry, heartbeat)
        return False
    heartbeat.stop()
    return True


def _drain_execution_outbox(target=None):
    try:
        entries = EXECUTION_OUTBOX.pending()
    except Exception as exc:
        _log_event(u"execution.outbox_read_failed", _exception_text(exc))
        raise
    for entry in entries:
        if not _same_target(entry["target"], target):
            continue
        try:
            plan = execution_session.PublicationPlan.from_records(entry["publication_items"])
            with EXECUTION_OUTBOX.publication_lease(entry) as acquired:
                if not acquired:
                    continue
                output_publisher.publish(plan)
                if not entry["publication_complete"]:
                    if isinstance(entry.get("result"), dict):
                        _mark_publication_observed(entry["result"], plan)
                    if hasattr(EXECUTION_OUTBOX, "replace_result") and isinstance(entry.get("result"), dict):
                        entry = EXECUTION_OUTBOX.replace_result(entry, entry["result"])
                    entry = EXECUTION_OUTBOX.mark_publication_complete(entry)
        except Exception as exc:
            _log_event(u"execution.output_publication_retry_failed", _exception_text(exc))
            continue
        try:
            acknowledged = EXECUTION_OUTBOX.deliver(entry, gateway_client)
        except Exception as exc:
            _log_event(u"execution.delivery_retry_failed", _exception_text(exc))
            _start_delivery_retry(entry)
            continue
        if not acknowledged:
            _start_delivery_retry(entry)


def _same_target(left, right):
    if not isinstance(left, dict) or not isinstance(right, dict):
        return False
    names = ("bridge_pid", "bridge_port", "arcmap_pid", "hwnd")
    try:
        return all(int(left.get(name) or 0) == int(right.get(name) or 0) for name in names)
    except (TypeError, ValueError):
        return False


def _start_delivery_retry(entry, heartbeat=None):
    run_id = entry["run_id"]
    with _DELIVERY_LOCK:
        if run_id in _DELIVERY_WORKERS:
            return
        if heartbeat is None:
            heartbeat = _ExecutionHeartbeat(run_id, entry["owner"])
            heartbeat.start()
        worker = _ExecutionDeliveryWorker(entry, heartbeat)
        _DELIVERY_WORKERS[run_id] = worker
        worker.start()


class _ExecutionDeliveryWorker(object):
    def __init__(self, entry, heartbeat, interval=2.0):
        self.entry = entry
        self.heartbeat = heartbeat
        self.interval = interval
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True

    def start(self):
        self.thread.start()

    def _run(self):
        run_id = self.entry["run_id"]
        try:
            while True:
                try:
                    acknowledged = EXECUTION_OUTBOX.deliver(self.entry, gateway_client)
                    if not acknowledged:
                        time.sleep(min(self.interval, 0.5))
                        continue
                    self.heartbeat.stop()
                    _log_event(u"execution.delivery_acknowledged", run_id)
                    return
                except Exception as exc:
                    _log_event(u"execution.delivery_retry_failed", _exception_text(exc))
                    time.sleep(self.interval)
        finally:
            with _DELIVERY_LOCK:
                _DELIVERY_WORKERS.pop(run_id, None)


class _ExecutionHeartbeat(object):
    def __init__(self, run_id, owner_id, interval=5.0):
        self.run_id = run_id
        self.owner_id = owner_id
        self.interval = interval
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True

    def start(self):
        self.thread.start()

    def stop(self):
        self.stopped.set()
        self.thread.join(10.0)

    def _run(self):
        while not self.stopped.wait(self.interval):
            try:
                gateway_client.heartbeat_run(self.run_id, self.owner_id)
            except Exception as exc:
                _log_event(u"execution.heartbeat_failed", _exception_text(exc))


def _sync_current_context(run_id=None, sync_token=None, phase=None, target=None):
    context = context_reader.read_context()
    if run_id:
        gateway_client.sync_run_context(run_id, context, sync_token, phase, target)
    return context


def _consume_silent_command():
    try:
        if not path_utils.isfile(SILENT_COMMAND_FILE):
            return {}
        with path_utils.open_binary(SILENT_COMMAND_FILE, "rb") as handle:
            raw = handle.read()
        if not isinstance(raw, unicode):
            raw = raw.decode("utf-8", "replace")
        payload = json.loads(raw.lstrip(u"\ufeff"))
        if float(payload.get("expires_at") or 0) < time.time():
            return {}
        action = payload.get("action")
        if action not in ("sync", "execute"):
            return {}
        try:
            path_utils.remove(SILENT_COMMAND_FILE)
        except OSError:
            pass
        return payload if isinstance(payload, dict) else {}
    except (IOError, OSError, ValueError, TypeError) as exc:
        _log_event(u"bridge.silent_command_failed", _exception_text(exc))
        return {}


def suppress_last_error_popup():
    return bool(_LAST_COMMAND_WAS_SILENT)


def _clear_silent_state():
    global _LAST_COMMAND_WAS_SILENT, _LAST_SILENT_COMMAND
    _LAST_COMMAND_WAS_SILENT = False
    _LAST_SILENT_COMMAND = {}


def _log_event(kind, detail=None):
    try:
        log_dir = path_utils.join_path(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "ArcMapAIAssistant", "logs")
        if not path_utils.isdir(log_dir):
            path_utils.makedirs(log_dir)
        path = path_utils.join_path(log_dir, "arcmap_runtime.log")
        message = u"%s\t%s\t%s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), kind, _unicode_text(detail or ""))
        with path_utils.open_binary(path, "ab") as handle:
            handle.write(message.encode("utf-8", "replace"))
    except (IOError, OSError):
        pass


def _exception_text(exc):
    return exception_text.exception_text(exc)


def _mark_publication_observed(result, publication_plan):
    """Publication is confirmed by output_publisher before this durable update."""
    published_paths = set(item.path for item in publication_plan.items)
    for step in result.get("steps", []):
        payload = step.get("result") or {}
        observation = payload.get("observation") or {}
        if observation.get("path") not in published_paths:
            continue
        observation["map_publication"] = "published"
        contract = observation.get("contract") or {}
        for check in contract.get("checks") or []:
            if check.get("name") == "map_publication" and check.get("expected") == "published":
                check["actual"] = "published"
                check["verdict"] = "passed"
                check.pop("proof", None)
        if contract:
            contract["verdict"] = "passed"


def _postcondition_failure(exc):
    if not isinstance(exc, workflow_executor.WorkflowExecutionError):
        return None
    if not exc.contract_path:
        return None
    return {
        "step_id": exc.step_id,
        "capability_id": exc.capability_id,
        "contract_path": exc.contract_path,
        "expected": exc.expected,
        "actual": exc.actual,
    }


def _traceback_text():
    try:
        return _unicode_text(traceback.format_exc())
    except (UnicodeDecodeError, UnicodeEncodeError, TypeError, ValueError):
        return u""


def _unicode_text(value):
    return exception_text.to_unicode(value)


def _confirm_direct_edit(message):
    if _LAST_COMMAND_WAS_SILENT:
        return bool(_LAST_SILENT_COMMAND.get("allow_edits"))
    text = _unicode_text(message) + u"\n\n这会直接修改原始数据，且不承诺可撤销。是否继续？"
    result = pythonaddins.MessageBox(text, "ArcMap AI Assistant", 4)
    if isinstance(result, bool):
        return result
    value = _unicode_text(result).lower()
    return value in (u"yes", u"y", u"true", u"1", u"6", u"是", u"确定")


try:
    _drain_execution_outbox()
except Exception as exc:
    _log_event(u"execution.startup_drain_failed", _exception_text(exc))
