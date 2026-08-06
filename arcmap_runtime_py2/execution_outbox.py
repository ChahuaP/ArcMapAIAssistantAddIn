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
        self._prune_orphan_guards()

    def enqueue(self, run_id, owner_id, status, result, target, publication_items):
        run_id = _run_id(run_id)
        owner_id = _owner_id(owner_id)
        if status not in ("executed", "failed"):
            raise ValueError("execution status is invalid.")
        if not isinstance(result, dict):
            raise ValueError("execution result must be an object.")
        publication_items = _publication_items(publication_items)
        if status == "failed" and publication_items:
            raise ValueError("failed execution cannot publish outputs.")
        entry = {
            "run_id": run_id,
            "owner": owner_id,
            "status": status,
            "result": result,
            "result_hash": result_hash(result),
            "target": _target(target),
            "publication_items": publication_items,
            "publication_complete": not publication_items,
        }
        destination = self._entry_path(run_id)
        if path_utils.isfile(destination):
            current = self._read(destination)
            if current != entry:
                raise ValueError("conflicting execution outbox entry.")
            return current
        self._write_atomic(destination, entry)
        return entry

    def replace_result(self, entry, result):
        if not isinstance(result, dict):
            raise ValueError("publication result must be an object.")
        run_id = _run_id(entry.get("run_id"))
        destination = self._entry_path(run_id)
        current = self._read(destination)
        if current["publication_complete"]:
            raise ValueError("execution outbox entry changed before publication result update.")
        updated = dict(current)
        updated["result"] = result
        updated["result_hash"] = result_hash(result)
        self._write_atomic(destination, updated)
        return updated

    def mark_publication_complete(self, entry):
        run_id = _run_id(entry.get("run_id"))
        destination = self._entry_path(run_id)
        if not path_utils.isfile(destination):
            raise ValueError("execution outbox entry is missing.")
        current = self._read(destination)
        if current != entry:
            raise ValueError("execution outbox entry changed before publication acknowledgement.")
        if current["publication_complete"]:
            return current
        updated = dict(current)
        updated["publication_complete"] = True
        self._write_atomic(destination, updated)
        return updated

    @contextmanager
    def publication_lease(self, entry):
        run_id = _run_id(entry.get("run_id"))
        guard = _delivery_guard(self._publication_guard_path(run_id), timeout_seconds=0.0)
        try:
            guard.__enter__()
        except IOError:
            yield False
            return
        try:
            yield True
        finally:
            guard.__exit__(None, None, None)

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
        with self.publication_lease(entry) as publication_idle:
            if not publication_idle:
                return False
            lease_owner = str(uuid.uuid4())
            if not self._acquire_delivery_lease(run_id, lease_owner):
                return False
            try:
                if not path_utils.isfile(entry_path):
                    return True
                stored = self._read(entry_path)
                if stored != entry:
                    raise ValueError("execution outbox entry changed before delivery.")
                if not stored["publication_complete"]:
                    raise ValueError("execution outputs have not been published.")
                client.complete_run(
                    stored["run_id"], stored["status"], stored["result"], stored["owner"],
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
            if not name.endswith(".lease") or name.endswith(".publication.lease"):
                continue
            run_id = name[:-len(".lease")]
            if not _canonical_uuid(run_id) or path_utils.isfile(self._entry_path(run_id)):
                continue
            try:
                path_utils.remove(path_utils.join_path(self.directory, name))
            except OSError:
                pass
        for name in path_utils.listdir(self.directory):
            if name.endswith(".publication.lease.guard"):
                run_id = name[:-len(".publication.lease.guard")]
                lease_path = u""
            elif name.endswith(".lease.guard"):
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

    def _lease_path(self, run_id):
        return path_utils.join_path(self.directory, run_id + ".lease")

    def _lease_guard_path(self, run_id):
        return path_utils.join_path(self.directory, run_id + ".lease.guard")

    def _publication_guard_path(self, run_id):
        return path_utils.join_path(self.directory, run_id + ".publication.lease.guard")

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
            tombstone = path + ".expired." + _owner_id(str(uuid.uuid4()))
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
        fallback_expiry = os.path.getmtime(path_utils.to_unicode_path(path)) + DELIVERY_LEASE_SECONDS
        with path_utils.open_binary(path, "rb") as handle:
            payload = handle.read()
        if not isinstance(payload, text_type):
            payload = payload.decode("ascii")
        try:
            lease = json.loads(payload)
        except (ValueError, TypeError):
            return {"owner": None, "claimed_at": fallback_expiry - DELIVERY_LEASE_SECONDS, "expires_at": fallback_expiry}
        if (
            not isinstance(lease, dict)
            or not isinstance(lease.get("owner"), text_type)
            or not _canonical_uuid(lease["owner"])
            or float(lease.get("expires_at") or 0) <= 0
        ):
            return {"owner": None, "claimed_at": fallback_expiry - DELIVERY_LEASE_SECONDS, "expires_at": fallback_expiry}
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
        if not isinstance(entry.get("owner"), text_type) or not entry["owner"]:
            raise ValueError("execution outbox owner is invalid.")
        if entry.get("status") not in ("executed", "failed") or not isinstance(entry.get("result"), dict):
            raise ValueError("execution outbox result is invalid.")
        publication_items = _publication_items(entry.get("publication_items"))
        if publication_items != entry.get("publication_items"):
            raise ValueError("execution publication items are not canonical.")
        if not isinstance(entry.get("publication_complete"), bool):
            raise ValueError("execution publication status is invalid.")
        if not publication_items and not entry["publication_complete"]:
            raise ValueError("empty execution publication is already complete.")
        if entry.get("status") == "failed" and publication_items:
            raise ValueError("failed execution cannot publish outputs.")
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


def _owner_id(value):
    value = _protocol_text(value, "execution owner_id")
    if not value:
        raise ValueError("execution owner_id is required.")
    return value


def _publication_items(values):
    if not isinstance(values, list):
        raise ValueError("execution publication items must be a list.")
    result = []
    seen = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("execution publication item must be an object.")
        path = _protocol_text(value.get("path"), "execution publication path")
        if not path:
            raise ValueError("execution publication path is required.")
        visible = value.get("visible")
        if visible is not None and not isinstance(visible, bool):
            raise ValueError("execution publication visibility is invalid.")
        selection_oids = value.get("selection_oids")
        if selection_oids is not None:
            if not isinstance(selection_oids, list) or any(
                not isinstance(item, integer_types) or isinstance(item, bool)
                for item in selection_oids
            ):
                raise ValueError("execution publication selection is invalid.")
            selection_oids = sorted(set(selection_oids))
        normalized = path_utils.normcase(path_utils.normpath(path))
        if normalized in seen:
            raise ValueError("execution publication items must be unique.")
        seen.add(normalized)
        result.append({
            "path": path,
            "visible": visible,
            "selection_oids": selection_oids,
        })
    return result


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
