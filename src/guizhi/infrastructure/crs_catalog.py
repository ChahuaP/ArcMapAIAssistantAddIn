"""Store immutable coordinate records without importing GIS libraries."""
import json


def store(workspace, kind, identifier, title, body):
    with workspace.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        revision = db.execute(
            "SELECT coalesce(max(revision), 0) + 1 FROM crs_catalog WHERE kind = ? AND id = ?",
            (kind, identifier),
        ).fetchone()[0]
        entry = {"kind": kind, "id": identifier, "revision": revision, "title": title, **body}
        db.execute("INSERT INTO crs_catalog VALUES (?, ?, ?, ?)",
                   (kind, identifier, revision, json.dumps(entry)))
    return entry


def catalog(workspace):
    with workspace.connect() as db:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT body FROM crs_catalog ORDER BY kind, id, revision DESC",
        )]


def get_plan(workspace, reference):
    identifier, _, revision = reference.partition("@")
    if not revision.isdecimal() or len(revision) > 8:
        raise ValueError("Specify the plan as id@revision")
    with workspace.connect() as db:
        row = db.execute("SELECT body FROM crs_catalog WHERE kind='plan' AND id=? AND revision=?",
                         (identifier, int(revision))).fetchone()
    if row is None:
        raise ValueError("Verified transformation plan not found")
    return json.loads(row[0])
