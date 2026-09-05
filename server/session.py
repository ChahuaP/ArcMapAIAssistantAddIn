"""Bridge session: the execution orchestrator of the boundary server.

One BridgeSession owns the live ArcMap target, the captured context lease and
single-flight dispatched runs. It speaks the Bridge lease protocol exactly as
the Py2 runtime expects: dispatch with the fenced triple, lease-ack returning
the workflow row, receipts/probes/samples routed back from the callback HTTP
surface. Fencing failures reject hard — there is no auto-replay.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

from .bridge_discovery import list_bridge_targets
from .contracts import canonical_json, digest
from .paths import localappdata_dir

BRIDGE_TIMEOUT_SECONDS = 30.0
RECEIPT_TIMEOUT_SECONDS = 600.0
CONTEXT_LEASE_DIGEST = "context-lease"


class BridgeUnavailable(RuntimeError):
    """No live Bridge target; the operator must open ArcMap first."""


class LeaseFenceError(ValueError):
    """A callback failed lease fencing (stale or foreign triple)."""


class _Waiter:
    __slots__ = ("event", "document")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.document: Optional[Dict[str, Any]] = None


class _RunRecord:
    __slots__ = ("run_id", "op_id", "operation", "lease_id", "epoch", "plan_hash",
                 "step", "staging_root", "content_hash", "target", "receipt")

    def __init__(self, run_id: str, op_id: str, operation: str, lease_id: str,
                 epoch: int, plan_hash: str, step: Dict[str, Any],
                 staging_root: str, content_hash: str, target: Dict[str, Any]):
        self.run_id = run_id
        self.op_id = op_id
        self.operation = operation
        self.lease_id = lease_id
        self.epoch = epoch
        self.plan_hash = plan_hash
        self.step = step
        self.staging_root = staging_root
        self.content_hash = content_hash
        self.target = target
        self.receipt: Optional[Dict[str, Any]] = None


class BridgeSession:
    """Owns target binding, context capture and fenced single-run execution."""

    def __init__(self, deployment_hash: str = ""):
        self.deployment_hash = str(deployment_hash or
                                   os.environ.get("BOUNDARY_DEPLOYMENT_HASH", ""))
        self._lock = threading.Lock()
        self._exec_lock = threading.Lock()
        self._context: Optional[Dict[str, Any]] = None
        self._context_waiters: Dict[str, _Waiter] = {}
        self._receipt_waiters: Dict[str, _Waiter] = {}
        self._probe_waiters: Dict[str, _Waiter] = {}
        self._sample_waiters: Dict[str, _Waiter] = {}
        self._runs: Dict[str, _RunRecord] = {}
        self._context_lease: Dict[str, Any] = {}
        self._context_dirty = False

    def _resolve_deployment_hash(self, target: Dict[str, Any]) -> str:
        """The live Bridge's own reported hash is the authoritative identity."""
        if self.deployment_hash:
            return self.deployment_hash
        self.deployment_hash = str(target.get("deployment_hash")
                                   or target.get("source_sha256") or "")
        return self.deployment_hash

    # -- discovery ---------------------------------------------------------

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        """Windows-correct process existence probe.

        ``os.kill(pid, 0)`` is a POSIX idiom: on Windows it sends a console
        control event (or fails outright) for GUI processes like ArcMap, so
        liveness must go through OpenProcess instead.
        """
        value = int(pid or 0)
        if value <= 0:
            return False
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            ERROR_ACCESS_DENIED = 5
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, value)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return kernel32.GetLastError() == ERROR_ACCESS_DENIED
        try:
            os.kill(value, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False

    @classmethod
    def filter_live_targets(cls, targets: List[Dict[str, Any]],
                            is_alive=None) -> List[Dict[str, Any]]:
        """Keep only targets whose ArcMap AND Bridge processes still exist.

        The Bridge is an independent EXE: after ArcMap closes it may linger
        with a stale ready-file and a healthy /health, so process liveness is
        checked here, on the boundary side.
        """
        alive = is_alive or cls._pid_alive
        live = []
        for target in targets:
            if (alive(target.get("arcmap_pid", 0))
                    and alive(target.get("bridge_pid", 0))):
                live.append(target)
        return live

    def targets(self) -> List[Dict[str, Any]]:
        try:
            targets = list_bridge_targets()
        except (OSError, RuntimeError, ValueError) as exc:
            raise BridgeUnavailable(
                "未找到运行中的 ArcMap Bridge：%s。请先打开 ArcMap 并加载 Bridge，再重试。" % exc)
        live = self.filter_live_targets(targets)
        if not live:
            raise BridgeUnavailable(
                "Bridge 进程仍在运行，但 ArcMap 已关闭（无存活地图目标）。"
                "请重新打开 ArcMap 并点击 Add-in 按钮。")
        return live

    def _active_target(self) -> Dict[str, Any]:
        targets = self.targets()
        active = [target for target in targets if target.get("active")]
        chosen = active[0] if active else targets[0]
        return chosen

    # -- context -----------------------------------------------------------

    def context_view(self, force: bool = False) -> Dict[str, Any]:
        """Model-facing context. Recaptures when a mutation may have changed
        the map (``force`` for reads that must be live, dirty after writes);
        a frozen cache would make the agent chase stale state and would fail
        every later dispatch on content-hash drift."""
        if self._context is not None:
            target = self._context.get("target") or {}
            if not (self._pid_alive(target.get("arcmap_pid", 0))
                    and self._pid_alive(target.get("bridge_pid", 0))):
                # The cached target's ArcMap/Bridge died (e.g. ArcMap was
                # restarted); a stale hwnd would fail every dispatch.
                self._context = None
                self._context_dirty = True
        if force or self._context_dirty or self._context is None:
            self.capture_context()
            self._context_dirty = False
        context = self._context or {}
        return {
            "mxd_path": context.get("mxd", ""),
            "data_frame": context.get("data_frame", ""),
            "content_hash": context.get("content_hash", ""),
            "captured_at": context.get("captured_at"),
            "layers": context.get("layers", []),
            "target": {
                name: context.get("target", {}).get(name)
                for name in ("arcmap_pid", "bridge_pid", "bridge_port", "hwnd")
            },
        }

    def capture_context(self, timeout: float = BRIDGE_TIMEOUT_SECONDS) -> Dict[str, Any]:
        run_id = str(uuid.uuid4())
        try:
            target = self._active_target()
        except BridgeUnavailable:
            # Never keep serving a dead target from the frozen snapshot.
            self._context = None
            self._context_dirty = True
            raise
        lease = {
            "lease_id": str(uuid.uuid4()), "run_id": run_id, "epoch": 1,
            "plan_digest": CONTEXT_LEASE_DIGEST, "hwnd": target["hwnd"],
            "bridge_port": target["bridge_port"], "arcmap_pid": target["arcmap_pid"],
        }
        self._context_lease = lease
        waiter = self._register(self._context_waiters, run_id)
        result = self._post(target["bridge_port"], "/capture-context", {
            "run_id": run_id, "phase": "before_planning", "hwnd": target["hwnd"],
            "lease_id": lease["lease_id"], "epoch": lease["epoch"],
            "plan_hash": lease["plan_digest"],
        })
        if isinstance(result, dict) and (result.get("layers") or result.get("mxd_path")):
            document = result
        else:
            if not waiter.event.wait(timeout=timeout):
                raise BridgeUnavailable("上下文回调超时：Py2 runtime 未响应（%ss）。" % timeout)
            document = waiter.document
        if not isinstance(document, dict) or not document.get("content_hash"):
            raise RuntimeError("ArcMap 上下文回调缺少 content_hash。")
        self._context = {
            "mxd": document.get("mxd_path", ""),
            "data_frame": document.get("data_frame", ""),
            "content_hash": str(document.get("content_hash")),
            "layers": document.get("layers", []),
            "edit_session_state": document.get("edit_session_state"),
            "is_saved": document.get("is_saved"),
            "captured_at": time.time(),
            "target": target,
        }
        return self._context

    def context_digest(self) -> str:
        context = self._context or {}
        return context.get("content_hash", "")

    # -- status --------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Live boundary/bridge/arcmap facts for the status panel and tools.

        Distinguishes the three states users conflate: the boundary itself,
        the Bridge EXE (may outlive ArcMap), and live ArcMap targets.
        """
        document: Dict[str, Any] = {
            "boundary": {"ok": True, "pid": os.getpid()},
            "bridge": {"health_ok": False, "process_alive": False,
                       "pid": None, "port": None, "ready_file": False},
            "arcmap": {"targets": [], "alive_count": 0},
            "context": {"captured": self._context is not None,
                        "dirty": self._context_dirty},
        }
        ready = self._read_ready_file()
        if ready:
            document["bridge"]["ready_file"] = True
            document["bridge"]["pid"] = ready.get("pid")
            document["bridge"]["port"] = ready.get("port")
            alive = self._pid_alive(ready.get("pid") or 0)
            document["bridge"]["process_alive"] = alive
            document["bridge"]["ready_file_stale"] = not alive
            # Skip the health probe for a dead pid: hitting the dead port
            # just burns the poll timeout on a foregone conclusion.
            if alive:
                document["bridge"]["health_ok"] = bool(self._health(ready.get("port") or 0))
        try:
            targets = self.targets()
            # NOTE: the Bridge's per-target "active" flag only mirrors which
            # ArcMap window is foreground right now; exposing it made the
            # model ask users to "activate" ArcMap for no reason.
            document["arcmap"]["targets"] = [
                {"arcmap_pid": t["arcmap_pid"], "bridge_pid": t["bridge_pid"],
                 "bridge_port": t["bridge_port"], "hwnd": t["hwnd"]}
                for t in targets]
            document["arcmap"]["alive_count"] = len(targets)
        except BridgeUnavailable as exc:
            document["arcmap"]["offline_reason"] = str(exc)
        if self._context is not None:
            document["context"]["captured_at"] = self._context.get("captured_at")
            document["context"]["content_hash"] = self._context.get("content_hash")
        return document

    @staticmethod
    def _read_ready_file() -> Optional[Dict[str, Any]]:
        from .bridge_discovery import ready_file_path
        path = ready_file_path()
        try:
            with open(path, "r", encoding="utf-8") as stream:
                document = json.load(stream)
            return document if isinstance(document, dict) else None
        except (OSError, ValueError):
            return None

    def _health(self, bridge_port: int) -> bool:
        if bridge_port <= 0:
            return False
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:%d/health" % int(bridge_port), timeout=1.5) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return isinstance(payload, dict) and payload.get("ok") is True
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return False

    # -- execution ---------------------------------------------------------

    def execute(self, op_id: str, operation_id: str, arguments: Dict[str, Any],
                card: Dict[str, Any],
                timeout: float = RECEIPT_TIMEOUT_SECONDS) -> Dict[str, Any]:
        """Run one operation: fenced dispatch, wait receipt, return result.

        Single-flight: the Bridge is a single-writer executor; concurrent
        dispatches would break lease fencing on the ArcMap side.
        """
        with self._exec_lock:
            if self._context_dirty or self._context is None:
                self.capture_context()
                self._context_dirty = False
            context = self._context or {}
            target = context["target"]
            step = {
                "id": "step_1", "operation": operation_id,
                "arguments": dict(arguments),
                "reason": card.get("summary", operation_id),
            }
            plan_hash = digest(canonical_json(step))
            run_id = str(uuid.uuid4())
            lease_id = str(uuid.uuid4())
            effects = card.get("side_effects", "read_only")
            if isinstance(effects, list):
                effects = effects[0] if effects else "read_only"
            allow_edits = effects == "edits_data"
            record = _RunRecord(
                run_id=run_id, op_id=op_id, operation=operation_id,
                lease_id=lease_id, epoch=2, plan_hash=plan_hash, step=step,
                staging_root=str(localappdata_dir() / "staging" / run_id),
                content_hash=context.get("content_hash", ""), target=target,
            )
            receipt_waiter = self._register(self._receipt_waiters, run_id)
            with self._lock:
                self._runs[run_id] = record
            self._post(target["bridge_port"], "/dispatch", {
                "lease_id": lease_id, "epoch": record.epoch,
                "plan_hash": plan_hash, "run_id": run_id,
                "allow_edits": allow_edits, "hwnd": target["hwnd"],
                "context_snapshot": {"sealed_content_hash": record.content_hash},
            }, timeout=120.0)
            # Any dispatch may have mutated the map (even a failed one may
            # stage partial writes); the cached context is stale from here on.
            self._context_dirty = True
            if not receipt_waiter.event.wait(timeout=timeout):
                return {
                    "status": "indeterminate",
                    "message": "执行已分发但回执未在 %ss 内到达；禁止自动重放，请用 verify_result 或人工核查。" % timeout,
                    "run_id": run_id,
                }
            receipt = receipt_waiter.document or {}
            self._fence(record.lease_id, record.epoch, record.plan_hash, receipt,
                        "receipt")
            record.receipt = receipt
            if receipt.get("status") != "executed":
                return {
                    "status": "failed",
                    "message": (receipt.get("result") or {}).get("error")
                               or "ArcMap 运行时报告执行失败。",
                    "receipt": receipt,
                }
            return {
                "status": "executed",
                "op_id": op_id,
                "run_id": run_id,
                "lease_id": lease_id,
                "epoch": record.epoch,
                "plan_hash": plan_hash,
                "result": receipt.get("result") or {},
            }

    # -- verification ------------------------------------------------------

    def verify(self, op_id: str, card: Dict[str, Any],
               timeout: float = BRIDGE_TIMEOUT_SECONDS) -> Dict[str, Any]:
        """Trigger an independent ArcPy acceptance probe for one operation."""
        record = self._find_record_by_op(op_id)
        if record is None:
            raise KeyError("op_id %s 没有对应的执行记录。" % op_id)
        target = record.target
        waiter = self._register(self._probe_waiters, "%s:map" % record.run_id)
        self._post(target["bridge_port"], "/acceptance-probe", {
            "lease_id": record.lease_id, "epoch": record.epoch,
            "plan_hash": record.plan_hash, "run_id": record.run_id,
            "deployment_hash": self._resolve_deployment_hash(target), "hwnd": target["hwnd"],
            "probe_type": "map_state", "output_id": "map",
            "postcondition": (card.get("postconditions") or [{}])[0],
            "arguments": record.step.get("arguments", {}),
        })
        if not waiter.event.wait(timeout=timeout):
            return {"status": "unavailable", "message": "验收探针未在 %ss 内返回。" % timeout}
        return waiter.document or {"status": "unavailable"}

    # -- callback entry points (invoked by callbacks.py) --------------------

    def receive_context(self, run_id: str, payload: Dict[str, Any]) -> None:
        lease = self._context_lease or {}
        self._fence(lease.get("lease_id", ""), lease.get("epoch", 1),
                    lease.get("plan_digest", ""), payload, "context")
        self._deliver(self._context_waiters, run_id, payload.get("context"))

    def lease_ack(self, run_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = self._record(run_id)
        self._fence(record.lease_id, record.epoch, record.plan_hash, payload, "lease-ack")
        return {
            "lease": {
                "lease_id": record.lease_id, "run_id": record.run_id,
                "epoch": record.epoch, "plan_digest": record.plan_hash,
                "gateway_pid": os.getpid(),
                "arcmap_pid": record.target.get("arcmap_pid"),
                "bridge_pid": record.target.get("bridge_pid"),
                "bridge_port": record.target.get("bridge_port"),
                "target_hwnd": record.target.get("hwnd"),
                "deployment_hash": self._resolve_deployment_hash(record.target),
                "acquired_at": time.time(), "last_heartbeat": time.time(),
            },
            "run_id": record.run_id,
            "staging_root": record.staging_root,
            "workflow": {"action": "execute", "summary": "", "steps": [record.step]},
            "content_hash": record.content_hash,
        }

    def heartbeat(self, run_id: str, payload: Dict[str, Any]) -> None:
        record = self._record(run_id)
        self._fence(record.lease_id, record.epoch, record.plan_hash, payload, "heartbeat")

    def receive_receipt(self, run_id: str, payload: Dict[str, Any]) -> None:
        record = self._record(run_id)
        self._fence(record.lease_id, record.epoch, record.plan_hash, payload, "receipt")
        self._deliver(self._receipt_waiters, run_id, payload)

    def receive_sample(self, run_id: str, payload: Dict[str, Any]) -> None:
        token = "%s:%s" % (run_id, payload.get("layer_ref", ""))
        self._deliver(self._sample_waiters, token, payload.get("values"))

    def receive_probe(self, run_id: str, payload: Dict[str, Any]) -> None:
        document = payload.get("document") or {}
        probe_type = document.get("probe_type")
        output_id = "__unit__" if probe_type == "unit" else document.get("output_id", "")
        if probe_type == "map_state":
            output_id = "map"
        token = "%s:%s" % (run_id, output_id)
        self._deliver(self._probe_waiters, token, document)

    # -- internals -----------------------------------------------------------

    def _record(self, run_id: str) -> _RunRecord:
        with self._lock:
            record = self._runs.get(run_id)
        if record is None:
            raise LeaseFenceError("回调指向未知 run：%s。" % run_id)
        return record

    def _find_record_by_op(self, op_id: str) -> Optional[_RunRecord]:
        with self._lock:
            for record in self._runs.values():
                if record.op_id == op_id:
                    return record
        return None

    @staticmethod
    def _fence(lease_id: str, epoch: int, plan_hash: str,
               payload: Dict[str, Any], kind: str) -> None:
        if payload.get("lease_id") != lease_id:
            raise LeaseFenceError("%s 回执 lease_id 不匹配。" % kind)
        if int(payload.get("epoch") or 0) != int(epoch):
            raise LeaseFenceError("%s 回执 epoch 过期或不匹配。" % kind)
        if payload.get("plan_hash") != plan_hash:
            raise LeaseFenceError("%s 回执 plan_hash 不匹配。" % kind)

    def _register(self, table: Dict[str, _Waiter], token: str) -> _Waiter:
        waiter = _Waiter()
        with self._lock:
            table[token] = waiter
        return waiter

    def _deliver(self, table: Dict[str, _Waiter], token: str,
                 document: Optional[Dict[str, Any]]) -> bool:
        with self._lock:
            waiter = table.pop(token, None)
        if waiter is None:
            return False
        waiter.document = document if isinstance(document, dict) else {"raw": document}
        waiter.event.set()
        return True

    def _post(self, bridge_port: int, path: str,
              payload: Dict[str, Any],
              timeout: float = BRIDGE_TIMEOUT_SECONDS) -> Dict[str, Any]:
        request = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (int(bridge_port), path),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError("Bridge %s 失败：%s" % (path, exc.read().decode("utf-8", "replace")))
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise BridgeUnavailable("Bridge %s 不可达：%s" % (path, exc))
        if not isinstance(result, dict) or result.get("ok") is False:
            raise RuntimeError("Bridge %s 返回错误：%s" % (path, result))
        return result
