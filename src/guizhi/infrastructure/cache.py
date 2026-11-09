"""Bounded reuse of verified jobs and deterministic computations."""
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import time

from guizhi.infrastructure.workspace import SOURCE_ROOT
from guizhi.infrastructure.vector import digest

MAX_ENTRIES = 512
MAX_BYTES = 32 << 20
MAX_ENTRY = 1 << 20

READ_OPERATIONS = {
    "data.describe", "vector_inventory", "control_check", "semantics.read_documents",
    "semantics.approval_case", "knowledge.extract_facts", "knowledge.map_terms", "knowledge.match_objects",
    "knowledge.find_rules", "knowledge.model_scope", "governance.explain_findings",
    "presentation.export_table",
}
RUN_ARGUMENTS = {"document_run", "catalog_run", "case_run", "rules_run", "spatial_run", "prior_case"}
VALIDATOR_REVISION = "1.0.0"


def fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_stamp(path):
    value = path.stat()
    return value.st_size, value.st_mtime_ns, value.st_ino


def input_signature(item):
    path = Path(item["file"])
    layer = item.get("layer")
    # SQLite writers can modify a sidecar while leaving the main file untouched.
    sidecars = [path.with_name(path.name + suffix) for suffix in ("-wal", "-journal")] if layer else []
    if any(sidecar.exists() for sidecar in sidecars):
        return None
    stamp = file_stamp(path)
    if layer:
        from guizhi.infrastructure.operations.runner import data_revision

        revision = data_revision(path)
    else:
        revision = digest(path)
    if stamp != file_stamp(path) or any(sidecar.exists() for sidecar in sidecars):
        return None
    if item.get("revision") and item["revision"] != revision:
        raise ValueError("Input differs from its expected snapshot revision")
    return {"layer": layer, "format": path.suffix.lower(), "revision": revision}


@lru_cache(maxsize=96)
def implementation(operation):
    root = SOURCE_ROOT / "src" / "guizhi" / "infrastructure"
    modules = {
        "control_check": [root / "control.py", root / "coordinates.py"],
        "vector_inventory": [],
        "semantics.read_documents": [root / "documents.py"],
        "governance.explain_findings": [root / "operations" / "semantics.py", root / "operations" / "knowledge.py"],
    }
    paths = [SOURCE_ROOT / "VERSION", SOURCE_ROOT / "requirements.lock", root / "vector.py",
             root / "operations" / "runner.py", *modules.get(operation, ())]
    if "." in operation:
        paths.append(root / "operations" / (operation.split(".", 1)[0] + ".py"))
    if operation == "control_check":
        paths.append(root / "cache.py")
    return fingerprint([VALIDATOR_REVISION, *map(digest, paths)])


def read_entry(workspace, namespace, key):
    with workspace.connect() as db:
        row = db.execute("SELECT body FROM compute_cache WHERE namespace=? AND key=?", (namespace, key)).fetchone()
        if row:
            db.execute("UPDATE compute_cache SET touched=? WHERE namespace=? AND key=?", (time.time(), namespace, key))
            return json.loads(row[0])
    return None


def write_entry(workspace, namespace, key, value):
    body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    size = len(body.encode("utf-8"))
    if size > MAX_ENTRY:
        return
    with workspace.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("""INSERT INTO compute_cache VALUES (?, ?, ?, ?, ?)
                      ON CONFLICT(namespace, key) DO UPDATE SET body=excluded.body, size=excluded.size,
                      touched=excluded.touched""", (namespace, key, body, size, time.time()))
        count, total = db.execute("SELECT count(*), coalesce(sum(size), 0) FROM compute_cache").fetchone()
        if count > MAX_ENTRIES or total > MAX_BYTES:
            ids = []
            for row in db.execute("SELECT rowid, size FROM compute_cache ORDER BY touched, rowid"):
                if count <= MAX_ENTRIES and total <= MAX_BYTES:
                    break
                ids.append((row[0],))
                count -= 1
                total -= row[1]
            db.executemany("DELETE FROM compute_cache WHERE rowid=?", ids)


def record_dependencies(record):
    request = record["task"].get("request") or {}
    parameters = request.get("parameters", {})
    ids = {value for name, value in parameters.items() if name in RUN_ARGUMENTS and isinstance(value, str)}
    result = record.get("result") or {}
    if "workflow" in result:
        ids.update(step["run_id"] for step in result["workflow"]["steps"])
    if record["task"]["operation"] == "knowledge.register_catalog":
        for name in ("editions", "terms", "models"):
            for item in parameters.get(name, []):
                ref = item.get("basis") or item.get("assessment")
                if ref:
                    ids.add(ref["document_run"])
    return ids


def verified_run(workspace, run_id, visited=None):
    from guizhi.infrastructure.operations.evidence import artifact_path

    seen = set() if visited is None else visited
    if run_id in seen:
        return True
    seen.add(run_id)
    try:
        record = workspace.read(run_id)
        if record["status"] != "succeeded":
            return False
        for ref in record["result"]["evidence"]:
            file = artifact_path(workspace, run_id, ref["path"])
            if not file.is_file() or digest(file) != ref["sha256"]:
                return False
        return all(verified_run(workspace, identity, seen) for identity in record_dependencies(record))
    except (ValueError, OSError, KeyError):
        return False


def reuse_job(workspace, operation, inputs, parameters, execute, *, check=None):
    if operation not in READ_OPERATIONS:
        return execute(), False
    signatures = [input_signature(item) for item in inputs]
    if any(item is None for item in signatures):
        return execute(), False
    dependencies = {name: value for name, value in parameters.items() if name in RUN_ARGUMENTS and value}
    if not all(verified_run(workspace, identity) for identity in dependencies.values()):
        return execute(), False
    key = fingerprint([operation, implementation(operation), signatures, parameters])
    cached = read_entry(workspace, "job", key)
    if check:
        check()
    if cached and verified_run(workspace, cached["run_id"]):
        if signatures == [input_signature(item) for item in inputs]:
            return workspace.read(cached["run_id"]), True
    record = execute()
    if record["status"] == "succeeded" and signatures == [input_signature(item) for item in inputs]:
        write_entry(workspace, "job", key, {"run_id": record["id"]})
    return record, False
