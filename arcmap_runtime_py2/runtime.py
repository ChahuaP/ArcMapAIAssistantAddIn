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
    import acceptance_probe
    import bridge_process
    import deployment_identity
    import context_reader
    import execution_session
    import execution_outbox
    import exception_text
    import gateway_client
    import runtime_gate
    import path_utils
    from shared_runtime import platform_paths
    import workflow_executor
except ImportError:
    from . import arcmap_ui_dispatch
    from . import acceptance_probe
    from . import bridge_process
    from . import deployment_identity
    from . import context_reader
    from . import execution_session
    from . import execution_outbox
    from . import exception_text
    from . import gateway_client
    from . import runtime_gate
    from . import path_utils
    from shared_runtime import platform_paths
    from . import workflow_executor


try:
    unicode
except NameError:
    unicode = str


REPO_ROOT = path_utils.abspath(path_utils.join_path(os.path.dirname(__file__), ".."))
OPEN_WEB_CMD = path_utils.join_path(REPO_ROOT, "OpenAssistantWeb.cmd")
BRIDGE_EXE = path_utils.join_path(REPO_ROOT, "bridge", "ArcMapBridge.exe")
CREATE_NO_WINDOW = 0x08000000
SILENT_COMMAND_FILE = path_utils.join_path(
    platform_paths.localappdata_path("bridge_command.json")
)
_LAST_COMMAND_WAS_SILENT = False
_LAST_SILENT_COMMAND = {}
_DELIVERY_WORKERS = {}
_DELIVERY_LOCK = threading.Lock()
EXECUTION_OUTBOX = execution_outbox.ExecutionOutbox(path_utils.join_path(
    platform_paths.localappdata_path("execution_outbox"),
))


def show_message(text):
    pythonaddins.MessageBox(_unicode_text(text), "ArcMap AI Assistant", 0)


def open_web():
    subprocess.Popen(
        [platform_paths.command_shell(), "/c", OPEN_WEB_CMD],
        cwd=REPO_ROOT,
        creationflags=CREATE_NO_WINDOW
    )


def bind_ui_thread():
    """Bind the calling (ArcMap UI) thread as the dispatch owner.

    The Add-in's ``onClick`` runs on the ArcMap UI thread and must call this
    idempotently before every ``open_or_handle_bridge_command()`` so the
    deferred execution layer knows which thread is permitted to run ArcPy.
    This forms the explicit C#/Add-in UI ownership boundary: the UI thread
    identifies itself, and ``arcmap_ui_dispatch.defer`` fails closed for any
    other thread.  Safe to call repeatedly on the same thread; a different
    thread is rejected.
    """
    arcmap_ui_dispatch.register_ui_owner()


def open_or_handle_bridge_command():
    command = _consume_silent_command()
    if command:
        _run_silent_command(command)
        return
    open_assistant()


def open_assistant():
    _clear_silent_state()
    # The console owns the boundary server (8765) and open_web() starts it when
    # missing (the launcher is idempotent). Nothing on this path calls the
    # gateway, so the ArcMap UI thread never waits on the console boot; the
    # console reports its own connection state once it comes up.
    open_web()
    bridge_process.ensure_running(BRIDGE_EXE)
    _sync_current_context()


def _run_silent_command(command):
    global _LAST_COMMAND_WAS_SILENT, _LAST_SILENT_COMMAND
    _LAST_SILENT_COMMAND = command
    _LAST_COMMAND_WAS_SILENT = True
    gateway_client.ensure_running()
    _drain_execution_outbox(command.get("target"))
    action = command.get("action")
    if action == "sync":
        _sync_current_context(
            command.get("run_id"),
            command.get("context"),
            command.get("lease_id"),
            command.get("epoch") or 0,
            command.get("plan_hash") or u"",
            command.get("phase"),
            command.get("target"),
        )
        return
    if action == "execute":
        run_id = command.get("run_id")
        target = command.get("target")
        lease_id = command.get("lease_id")
        epoch = command.get("epoch") or 0
        plan_hash = command.get("plan_hash") or u""
        context = command.get("context")
        _validate_lease_identity(run_id, lease_id, epoch, plan_hash)
        row = _acknowledge_lease(run_id, lease_id, epoch, plan_hash, target)
        heartbeat = _start_execution_heartbeat(run_id, lease_id, epoch, plan_hash)
        try:
            arcmap_ui_dispatch.defer(lambda: _run_deferred_execution(
                run_id, target, lease_id, epoch, plan_hash, context, row, heartbeat,
            ))
        except Exception as exc:
            _persist_execution_failure(
                run_id, target, lease_id, epoch, plan_hash, heartbeat, exc, u"arcmap_ui_dispatch",
            )
            raise
        _log_event(u"execution.dispatched_synchronous", run_id)
        return
    if action == "acceptance_probe":
        _run_acceptance_probe(command)
        return
    if action == "reconcile":
        _run_reconcile(command)
        return
    if action == "sample":
        _run_sample(command)
        return
    if action == "runtime_gate":
        runtime_gate.apply(command.get("context"))
        return
    raise RuntimeError(u"未知 Bridge 指令：%s" % _unicode_text(action))


def _run_acceptance_probe(command):
    run_id = command.get("run_id")
    lease_id = command.get("lease_id")
    epoch = command.get("epoch") or 0
    plan_hash = command.get("plan_hash") or u""
    request = command.get("context") if isinstance(command.get("context"), dict) else {}
    deployment_hash = request.get("deployment_hash") or u""
    _validate_lease_identity(run_id, lease_id, epoch, plan_hash)
    if deployment_hash != deployment_identity.deployment_hash():
        raise RuntimeError(u"Bridge deployment identity does not match installed Py2 runtime.")
    if request.get("source_publish_unit_path"):
        document = acceptance_probe.probe_unit(request.get("source_publish_unit_path"))
        document["probe_type"] = "unit"
        document["manifest_digest"] = acceptance_probe._digest(dict(
            (key, value) for key, value in document.items() if key != "manifest_digest"))
    elif request.get("probe_type") == "map_state":
        document = acceptance_probe.probe_map_state(
            request.get("output_id"), request.get("postcondition"), request.get("arguments"))
        document["acceptance_proofs"] = acceptance_probe.probe_contract(
            request.get("acceptance_contract"), document)
        document["manifest_digest"] = acceptance_probe._digest(dict(
            (key, value) for key, value in document.items() if key != "manifest_digest"))
    else:
        document = acceptance_probe.probe(
            request.get("output_id"), request.get("kind"), request.get("staged_path"),
            request.get("output_format"))
        document["acceptance_proofs"] = acceptance_probe.probe_contract(
            request.get("acceptance_contract"), document)
        document["manifest_digest"] = acceptance_probe._digest(dict(
            (key, value) for key, value in document.items() if key != "manifest_digest"))
    gateway_client.complete_acceptance_probe(run_id, document, lease_id, epoch, plan_hash, deployment_hash)


def _run_reconcile(command):
    run_id = command.get("run_id")
    lease_id = command.get("lease_id")
    epoch = command.get("epoch") or 0
    plan_hash = command.get("plan_hash") or u""
    target = command.get("target")
    _validate_lease_identity(run_id, lease_id, epoch, plan_hash)
    entry = EXECUTION_OUTBOX.reconcile(run_id, target, gateway_client)
    if entry["lease_id"] != lease_id or entry["epoch"] != epoch or entry["plan_hash"] != plan_hash:
        raise RuntimeError(u"Reconcile receipt fencing does not match the requested lease.")


def _run_sample(command):
    run_id = command.get("run_id")
    lease_id = command.get("lease_id")
    epoch = command.get("epoch") or 0
    plan_hash = command.get("plan_hash") or u""
    request = command.get("context")
    if not isinstance(request, dict):
        raise RuntimeError(u"Bridge sample command lacks a request document.")
    _validate_lease_identity(run_id, lease_id, epoch, plan_hash)
    values = context_reader.sample_values(request.get("layer_ref"), request.get("fields"),
                                          request.get("max_rows"), request.get("max_samples"))
    gateway_client.complete_sample(run_id, request.get("layer_ref"), values,
                                  lease_id, epoch, plan_hash)


def _acknowledge_lease(run_id, lease_id, epoch, plan_hash, target):
    _validate_lease_identity(run_id, lease_id, epoch, plan_hash)
    return gateway_client.acknowledge_lease(run_id, lease_id, epoch, plan_hash, target)


def _validate_lease_identity(run_id, lease_id, epoch, plan_hash):
    if not isinstance(run_id, unicode) or not run_id:
        raise RuntimeError(u"Bridge execute command lacks run_id.")
    if not isinstance(lease_id, unicode) or not lease_id:
        raise RuntimeError(u"Bridge execute command lacks lease_id.")
    if not isinstance(epoch, (int, long)) or epoch <= 0:
        raise RuntimeError(u"Bridge execute command lacks a valid epoch.")
    if not isinstance(plan_hash, unicode) or not plan_hash:
        raise RuntimeError(u"Bridge execute command lacks plan_hash.")
    try:
        parsed_run = unicode(uuid.UUID(run_id))
        parsed_lease = unicode(uuid.UUID(lease_id))
    except (ValueError, AttributeError, TypeError):
        raise RuntimeError(u"Bridge execute command identity is invalid.")
    if parsed_run != run_id:
        raise RuntimeError(u"Bridge execute command run_id is not canonical.")
    if parsed_lease != lease_id:
        raise RuntimeError(u"Bridge execute command lease_id is not canonical.")


def _start_execution_heartbeat(run_id, lease_id, epoch, plan_hash):
    heartbeat = _ExecutionHeartbeat(run_id, lease_id, epoch, plan_hash)
    heartbeat.start()
    return heartbeat


def _run_deferred_execution(run_id, target, lease_id, epoch, plan_hash, context, row, heartbeat):
    try:
        _execute_dispatched_run(
            run_id, target, lease_id, epoch, plan_hash, context, row, heartbeat, silent=True,
        )
    except Exception as exc:
        _log_event(u"execution.deferred_failed", _exception_text(exc))


def _execute_run(run_id, target, lease_id, epoch, plan_hash, context, silent=False):
    _validate_lease_identity(run_id, lease_id, epoch, plan_hash)
    row = _acknowledge_lease(run_id, lease_id, epoch, plan_hash, target)
    heartbeat = _start_execution_heartbeat(run_id, lease_id, epoch, plan_hash)
    return _execute_dispatched_run(
        run_id, target, lease_id, epoch, plan_hash, context, row, heartbeat, silent=silent,
    )


def _execute_dispatched_run(run_id, target, lease_id, epoch, plan_hash, context, row, heartbeat, silent=False):
    try:
        if not isinstance(context, dict) or not context.get("sealed_content_hash"):
            raise RuntimeError(u"执行命令缺少封存的 content_hash。")
        live_context = context_reader.read_context()
        if live_context.get("content_hash") != context["sealed_content_hash"]:
            raise RuntimeError(u"地图上下文已漂移，拒绝执行封存计划。")
        outcome = workflow_executor.execute(row, live_context, confirm_callback=_confirm_direct_edit)
        result = outcome.result
    except Exception as exc:
        result = {
            "ok": False,
            "error": _exception_text(exc),
            "traceback": _traceback_text(),
            "postcondition_failure": _postcondition_failure(exc),
        }
        _persist_publish_and_deliver(
            run_id, target, "failed", result, lease_id, epoch, plan_hash, heartbeat,
        )
        raise

    acknowledged = _persist_publish_and_deliver(
        run_id, target, "executed", result, lease_id, epoch, plan_hash, heartbeat,
    )
    if not silent:
        if acknowledged:
            show_message(u"工作流执行完成：%s" % result.get("summary", "succeeded"))
        else:
            show_message(u"工作流已执行完成，权威结果正在重试提交到本地网关。")


def _persist_execution_failure(run_id, target, lease_id, epoch, plan_hash, heartbeat, exc, phase):
    result = {
        "ok": False,
        "error": _exception_text(exc),
        "traceback": _traceback_text(),
        "postcondition_failure": None,
        "failure_phase": phase,
    }
    _persist_publish_and_deliver(
        run_id, target, "failed", result, lease_id, epoch, plan_hash, heartbeat,
    )


def _persist_publish_and_deliver(run_id, target, status, result, lease_id, epoch, plan_hash, heartbeat):
    try:
        entry = EXECUTION_OUTBOX.enqueue(
            run_id, lease_id, epoch, plan_hash, status, result, target,
        )
    except Exception as exc:
        heartbeat.stop()
        _log_event(u"execution.outbox_persist_failed", _exception_text(exc))
        raise
    # Execution has verified and published outputs. Delivery retries must
    # never execute geoprocessing or add layers again.
    try:
        acknowledged = EXECUTION_OUTBOX.deliver(entry, gateway_client)
    except Exception as exc:
        _log_event(u"execution.delivery_failed", _exception_text(exc))
        _start_delivery_retry(entry, lease_id, epoch, plan_hash, heartbeat)
        return False
    if not acknowledged:
        _start_delivery_retry(entry, lease_id, epoch, plan_hash, heartbeat)
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
        # Drain only retries delivery; publication already completed.
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


def _start_delivery_retry(entry, lease_id=None, epoch=None, plan_hash=None, heartbeat=None):
    run_id = entry["run_id"]
    with _DELIVERY_LOCK:
        if run_id in _DELIVERY_WORKERS:
            return
        if heartbeat is None:
            lease_id = lease_id or entry.get("lease_id")
            epoch = epoch or entry.get("epoch") or 0
            plan_hash = plan_hash or entry.get("plan_hash") or u""
            heartbeat = _ExecutionHeartbeat(run_id, lease_id, epoch, plan_hash)
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
    def __init__(self, run_id, lease_id, epoch, plan_hash, interval=5.0):
        self.run_id = run_id
        self.lease_id = lease_id
        self.epoch = epoch
        self.plan_hash = plan_hash
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
                gateway_client.heartbeat_run(self.run_id, self.lease_id, self.epoch, self.plan_hash)
            except Exception as exc:
                _log_event(u"execution.heartbeat_failed", _exception_text(exc))


def _sync_current_context(run_id=None, context=None, lease_id=None, epoch=0, plan_hash=u"", phase=None, target=None):
    if not isinstance(context, dict) or not context:
        context = context_reader.read_context()
    if run_id:
        gateway_client.sync_run_context(run_id, context, lease_id, epoch, plan_hash, phase, target)
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
        if action not in ("sync", "execute", "acceptance_probe", "reconcile", "sample", "runtime_gate"):
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
        log_dir = platform_paths.localappdata_path("logs")
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
    raise
