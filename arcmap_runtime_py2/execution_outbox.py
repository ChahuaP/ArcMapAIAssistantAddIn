# -*- coding: utf-8 -*-
from __future__ import absolute_import

import ctypes
import errno
import hashlib
import json
import msvcrt
import os
import time
import uuid
from contextlib import contextmanager

try:
    import path_utils
except ImportError:
    from . import path_utils


try:
    text_type = unicode
    string_types = (basestring,)
    integer_types = (int, long)
except NameError:
    text_type = str
    string_types = (str, bytes)
    integer_types = (int,)


MOVEFILE_REPLACE_EXISTING = 0x1
MOVEFILE_WRITE_THROUGH = 0x8
DELIVERY_LEASE_SECONDS = 30.0


def result_hash(result):
    payload = json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if not isinstance(payload, bytes):
        payload = payload.encode("ascii")
    return hashlib.sha256(payload).hexdigest()


class ExecutionOutbox(object):
    def __init__(self, directory):
        self.directory = path_utils.abspath(directory)
        if not path_utils.isdir(self.directory):
            path_utils.makedirs(self.directory)
        self.receipts_directory = path_utils.join_path(self.directory, "receipts")
        if not path_utils.isdir(self.receipts_directory):
            path_utils.makedirs(self.receipts_directory)
        self._prune_orphan_guards()

    def enqueue(self, run_id, lease_id, epoch, plan_hash, status, result, target):
        run_id = _run_id(run_id)
        lease_id = _lease_id(lease_id)
        if not isinstance(epoch, integer_types) or epoch <= 0:
            raise ValueError("execution epoch must be a positive int.")
        plan_hash = _protocol_text(plan_hash, "execution plan_hash")
        if not plan_hash:
            raise ValueError("execution plan_hash is required.")
        if status not in ("executed", "failed"):
            raise ValueError("execution status is invalid.")
        if not isinstance(result, dict):
            raise ValueError("execution result must be an object.")
        entry = {
            "run_id": run_id,
            "lease_id": lease_id,
            "epoch": int(epoch),
            "plan_hash": plan_hash,
            "status": status,
            "result": result,
            "result_hash": result_hash(result),
            "target": _target(target),
        }
        destination = self._entry_path(run_id)
        if path_utils.isfile(destination):
            current = self._read(destination)
            if current != entry:
                raise ValueError("conflicting execution outbox entry.")
            if not path_utils.isfile(self._receipt_path(run_id)):
                self._write_atomic(self._receipt_path(run_id), current)
            return current
        self._write_atomic(destination, entry)
        self._write_atomic(self._receipt_path(run_id), entry)
        return entry

    def pending(self):
        entries = []
        for name in sorted(path_utils.listdir(self.directory)):
            if not name.endswith(".json"):
                continue
            entries.append(self._read(path_utils.join_path(self.directory, name)))
        return entries

    def deliver(self, entry, client):
        run_id = _run_id(entry.get("run_id"))
        entry_path = self._entry_path(run_id)
        if not path_utils.isfile(entry_path):
            return True
        lease_owner = str(uuid.uuid4())
        if not self._acquire_delivery_lease(run_id, lease_owner):
            return False
        try:
            if not path_utils.isfile(entry_path):
                return True
            stored = self._read(entry_path)
            if stored != entry:
                raise ValueError("execution outbox entry changed before delivery.")
            client.post_execution_receipt(
                stored["run_id"], stored["status"], stored["result"],
                stored["lease_id"], stored["epoch"], stored["plan_hash"],
                stored["result_hash"], stored["target"],
            )
            try:
                path_utils.remove(entry_path)
            except OSError as exc:
                if getattr(exc, "errno", None) != errno.ENOENT and path_utils.isfile(entry_path):
                    raise
            return True
        finally:
            self._release_delivery_lease(run_id, lease_owner)

    @staticmethod
    def _write_atomic(destination, entry):
        temporary = destination + ".%s.tmp" % uuid.uuid4()
        payload = json.dumps(entry, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if not isinstance(payload, bytes):
            payload = payload.encode("ascii")
        try:
            with path_utils.open_binary(temporary, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            _atomic_replace(temporary, destination)
        finally:
            if path_utils.isfile(temporary):
                path_utils.remove(temporary)

    def drain(self, client):
        delivered = 0
        for entry in self.pending():
            if self.deliver(entry, client):
                delivered += 1
        return delivered

    def _prune_orphan_guards(self):
        for name in path_utils.listdir(self.directory):
            if not name.endswith(".lease"):
                continue
            run_id = name[:-len(".lease")]
            if not _canonical_uuid(run_id) or path_utils.isfile(self._entry_path(run_id)):
                continue
            try:
                path_utils.remove(path_utils.join_path(self.directory, name))
            except OSError:
                pass
        for name in path_utils.listdir(self.directory):
            if name.endswith(".lease.guard"):
                run_id = name[:-len(".lease.guard")]
                lease_path = self._lease_path(run_id)
            else:
                continue
            if not _canonical_uuid(run_id):
                continue
            if path_utils.isfile(self._entry_path(run_id)) or (lease_path and path_utils.isfile(lease_path)):
                continue
            try:
                path_utils.remove(path_utils.join_path(self.directory, name))
            except OSError:
                pass

    def _entry_path(self, run_id):
        return path_utils.join_path(self.directory, run_id + ".json")

    def _receipt_path(self, run_id):
        return path_utils.join_path(self.receipts_directory, run_id + ".json")

    def reconcile(self, run_id, target, client):
        run_id = _run_id(run_id)
        stored = self._read(self._receipt_path(run_id))
        if stored["target"] != _target(target):
            raise ValueError("execution receipt target does not match reconcile target.")
        client.post_execution_receipt(stored["run_id"], stored["status"], stored["result"],
                            stored["lease_id"], stored["epoch"], stored["plan_hash"],
                            stored["result_hash"], stored["target"])
        return stored

    def _lease_path(self, run_id):
        return path_utils.join_path(self.directory, run_id + ".lease")

    def _lease_guard_path(self, run_id):
        return path_utils.join_path(self.directory, run_id + ".lease.guard")

    def _acquire_delivery_lease(self, run_id, owner, now=None):
        return self._acquire_lease(
            self._lease_path(run_id), self._lease_guard_path(run_id), owner, now,
        )

    def _acquire_lease(self, path, guard_path, owner, now=None):
        claimed_at = time.time() if now is None else float(now)
        lease = {
            "owner": owner,
            "claimed_at": claimed_at,
            "expires_at": claimed_at + DELIVERY_LEASE_SECONDS,
        }
        with _delivery_guard(guard_path):
            if _create_exclusive_json(path, lease):
                return True
            current = self._read_lease(path)
            if float(current.get("expires_at") or 0) > claimed_at:
                return False
            tombstone = path + ".expired." + _lease_id(str(uuid.uuid4()))
            if not _atomic_move_no_replace(path, tombstone):
                return False
            try:
                return _create_exclusive_json(path, lease)
            finally:
                if path_utils.isfile(tombstone):
                    path_utils.remove(tombstone)

    def _release_delivery_lease(self, run_id, owner):
        self._release_lease(self._lease_path(run_id), self._lease_guard_path(run_id), owner)

    def _release_lease(self, path, guard_path, owner):
        with _delivery_guard(guard_path):
            if not path_utils.isfile(path):
                return
            current = self._read_lease(path)
            if current.get("owner") != owner:
                return
            path_utils.remove(path)

    @staticmethod
    def _read_lease(path):
        with path_utils.open_binary(path, "rb") as handle:
            payload = handle.read()
        if not isinstance(payload, text_type):
            payload = payload.decode("ascii")
        try:
            lease = json.loads(payload)
        except (ValueError, TypeError) as exc:
            raise RuntimeError("execution outbox lease is corrupt: %s" % exc)
        if (
            not isinstance(lease, dict)
            or not isinstance(lease.get("owner"), text_type)
            or not _canonical_uuid(lease["owner"])
            or float(lease.get("expires_at") or 0) <= 0
        ):
            raise RuntimeError("execution outbox lease is invalid")
        return lease

    @staticmethod
    def _read(path):
        with path_utils.open_binary(path, "rb") as handle:
            payload = handle.read()
        if not isinstance(payload, text_type):
            payload = payload.decode("utf-8")
        entry = json.loads(payload)
        if not isinstance(entry, dict):
            raise ValueError("execution outbox entry is invalid.")
        if not isinstance(entry.get("lease_id"), text_type) or not entry["lease_id"]:
            raise ValueError("execution outbox lease_id is invalid.")
        if not isinstance(entry.get("epoch"), integer_types) or entry.get("epoch") <= 0:
            raise ValueError("execution outbox epoch is invalid.")
        if not isinstance(entry.get("plan_hash"), text_type) or not entry["plan_hash"]:
            raise ValueError("execution outbox plan_hash is invalid.")
        if entry.get("status") not in ("executed", "failed") or not isinstance(entry.get("result"), dict):
            raise ValueError("execution outbox result is invalid.")
        if result_hash(entry.get("result")) != entry.get("result_hash"):
            raise ValueError("execution outbox result hash is invalid.")
        _run_id(entry.get("run_id"))
        _target(entry.get("target"))
        return entry


def _run_id(value):
    value = _protocol_text(value, "execution run_id")
    parsed = uuid.UUID(value)
    canonical = _protocol_text(str(parsed), "execution run_id")
    if value.lower() != canonical:
        raise ValueError("execution run_id must be a canonical UUID.")
    return canonical


def _canonical_uuid(value):
    try:
        value = _protocol_text(value, "execution UUID")
        return str(uuid.UUID(value)) == value.lower()
    except (ValueError, AttributeError, TypeError):
        return False


def _lease_id(value):
    value = _protocol_text(value, "execution lease_id")
    if not value:
        raise ValueError("execution lease_id is required.")
    return value


def _protocol_text(value, field):
    if not isinstance(value, string_types):
        raise ValueError(field + " is required.")
    if not isinstance(value, text_type):
        try:
            value = value.decode("ascii")
        except UnicodeDecodeError:
            raise ValueError(field + " must be ASCII text.")
    return value


def _target(value):
    if not isinstance(value, dict):
        raise ValueError("execution target is required.")
    target = dict((name, int(value.get(name) or 0)) for name in ("bridge_pid", "bridge_port", "arcmap_pid", "hwnd"))
    if any(item <= 0 for item in target.values()):
        raise ValueError("execution target requires bridge_pid, bridge_port, arcmap_pid and hwnd.")
    return target


def _atomic_replace(source, destination):
    replace = getattr(os, "replace", None)
    if replace is not None:
        replace(source, destination)
        return
    if os.name == "nt":
        succeeded = ctypes.windll.kernel32.MoveFileExW(
            path_utils.to_unicode_path(source), path_utils.to_unicode_path(destination),
            MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
        )
        if not succeeded:
            raise ctypes.WinError()
        return
    os.rename(source, destination)


def _atomic_move_no_replace(source, destination):
    if os.name == "nt":
        succeeded = ctypes.windll.kernel32.MoveFileExW(
            path_utils.to_unicode_path(source), path_utils.to_unicode_path(destination),
            MOVEFILE_WRITE_THROUGH,
        )
        if succeeded:
            return True
        if not path_utils.isfile(source) or path_utils.isfile(destination):
            return False
        raise ctypes.WinError()
    try:
        os.rename(source, destination)
        return True
    except OSError as exc:
        if getattr(exc, "errno", None) in (errno.ENOENT, errno.EEXIST):
            return False
        raise


@contextmanager
def _delivery_guard(path, timeout_seconds=1.0):
    handle = path_utils.open_binary(path, "a+b")
    try:
        handle.seek(0)
        if os.path.getsize(path_utils.to_unicode_path(path)) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        deadline = time.time() + float(timeout_seconds)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except IOError:
                if time.time() >= deadline:
                    raise
                time.sleep(0.005)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        handle.close()
        try:
            path_utils.remove(path)
        except OSError:
            pass


def _create_exclusive_json(path, value):
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if not isinstance(payload, bytes):
        payload = payload.encode("ascii")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(path_utils.to_unicode_path(path), flags, 0o600)
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.EEXIST or path_utils.isfile(path):
            return False
        raise
    try:
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise IOError("execution delivery lease write failed.")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True
