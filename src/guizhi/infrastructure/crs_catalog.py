"""Store immutable coordinate records without importing GIS libraries."""
import json


def insert(db, kind, identifier, title, body):
    revision = db.execute("SELECT coalesce(max(revision), 0) + 1 FROM crs_catalog WHERE kind=? AND id=?",
                          (kind, identifier)).fetchone()[0]
    entry = {**body, "kind": kind, "id": identifier, "revision": revision, "title": title}
    db.execute("INSERT INTO crs_catalog VALUES (?, ?, ?, ?)", (kind, identifier, revision, json.dumps(entry)))
    return entry


def store(workspace, kind, identifier, title, body):
    with workspace.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        return insert(db, kind, identifier, title, body)


def catalog(workspace):
    with workspace.connect() as db:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT body FROM crs_catalog ORDER BY kind, id, revision DESC",
        )]


def get(workspace, kind, reference):
    identifier, _, revision = reference.partition("@")
    if not revision.isdecimal() or len(revision) > 8:
        raise ValueError("请选择坐标记录的具体版本")
    with workspace.connect() as db:
        row = db.execute("SELECT body FROM crs_catalog WHERE kind=? AND id=? AND revision=?",
                         (kind, identifier, int(revision))).fetchone()
    if row is None:
        raise ValueError("所选坐标记录不存在")
    return json.loads(row[0])


def get_plan(workspace, reference):
    return get(workspace, "plan", reference)


def rename(workspace, kind, reference, title):
    if kind not in {"definition", "plan"} or not isinstance(title, str) or not 1 <= len(title.strip()) <= 120:
        raise ValueError("请填写 1 至 120 字的名称")
    entry = get(workspace, kind, reference)
    with workspace.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT max(revision) FROM crs_catalog WHERE kind=? AND id=?",
                             (kind, entry["id"])).fetchone()[0]
        if current != entry["revision"]:
            raise ValueError("坐标记录已有新版本，请刷新后重试")
        if entry["title"] == title.strip():
            return entry
        if kind == "plan":
            entry["plan"]["title"] = title.strip()
        return insert(db, kind, entry["id"], title.strip(), entry)
