"""Workspace-local source registry and loopback document/vector services."""
import base64
import hashlib
import json
import math
import re
import sqlite3
import urllib.error
import urllib.request
from contextlib import closing
from datetime import date
from itertools import islice
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from guizhi.infrastructure.documents import decode_text_data

ENDPOINTS = {"Docling": 5001, "Qdrant": 6333, "Ollama": 11434}
TOOL_CONTRACTS = {
    "knowledge.search": {
        "title": "检索知识资料", "tool": "search_knowledge",
        "purpose": "核对已导入的政策、术语和业务依据。没有相关证据时返回空列表。",
        "inputs": [], "parameters": {"query": "必填字符串，1–500 字",
                                    "region": "可选地区，空值查询全部地区",
                                    "on_date": "可选 YYYY-MM-DD，默认当天"},
        "outputs": {"sources": "证据数组，含 id、资料名、定位信息、原文片段和有效期"},
        "map_effect": "只读，无地图影响",
    },
    "knowledge.read": {
        "title": "读取知识资料原文", "tool": "read_knowledge_source",
        "purpose": "按检索返回的证据编号读取精确出处和原文片段。",
        "inputs": [], "parameters": {"passage_id": "必填，knowledge.search 返回的证据 id"},
        "outputs": {"source": "资料名、定位信息、原文和有效期；已删除时为空"},
        "map_effect": "只读，无地图影响",
    },
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def service(name, path, payload=None, method=None):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(
        f"http://127.0.0.1:{ENDPOINTS[name]}{path}",
        None if payload is None else json.dumps(payload).encode("utf-8"),
        {"Content-Type": "application/json"}, method=method)
    try:
        with opener.open(request, timeout=150) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError) as error:
        raise ValueError(f"{name} 服务请求失败，请检查本机服务和配置") from error


def connect(root):
    folder = Path(root) / "knowledge"
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder / "index.sqlite", timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, region TEXT, version TEXT,
            start TEXT, end TEXT, source BLOB NOT NULL
        );
        CREATE TABLE IF NOT EXISTS passages (
            id TEXT PRIMARY KEY, document TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            location TEXT NOT NULL, text TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS lexical USING fts5(id UNINDEXED, text, tokenize='trigram');
        CREATE TRIGGER IF NOT EXISTS passages_removed AFTER DELETE ON passages BEGIN
            DELETE FROM lexical WHERE id=OLD.id;
        END;
        CREATE TRIGGER IF NOT EXISTS passages_added AFTER INSERT ON passages BEGIN
            INSERT INTO lexical VALUES (NEW.id, NEW.text);
        END;
    """)
    return db


def model(db):
    row = db.execute("SELECT value FROM settings WHERE name='model'").fetchone()
    if row is None:
        raise ValueError("请先在「知识与资料」选择本机向量模型")
    return row[0]


def embed(name, texts):
    vectors = service("Ollama", "/api/embed", {"model": name, "input": texts})["embeddings"]
    if len(vectors) != len(texts) or not vectors or not vectors[0] or any(
        len(v) != len(vectors[0]) or not all(math.isfinite(x) for x in v) for v in vectors
    ):
        raise ValueError("向量模型返回的数据不完整")
    return vectors


def collection(root, name):
    return "guizhi_" + hashlib.sha256((str(Path(root).resolve()) + "\n" + name).encode()).hexdigest()[:24]


def documents(db):
    return [dict(row) for row in db.execute("SELECT id,name,region,version,start,end FROM documents ORDER BY name,id")]


def parse(file, content):
    if file.suffix.lower() in (".txt", ".md"):
        text = decode_text_data(content, file.suffix).splitlines()
        for offset in range(0, len(text), 12):
            section = "\n".join(text[offset:offset + 12]).strip()
            if section:
                yield f"第 {offset + 1}–{min(len(text), offset + 12)} 行", section
        return
    response = service("Docling", "/v1/convert/source", {
        "sources": [{"kind": "file", "base64_string": base64.b64encode(content).decode("ascii"),
                     "filename": file.name}],
        "options": {"to_formats": ["json"], "do_ocr": file.suffix.lower() == ".pdf", "include_images": False, "ocr_preset": "rapidocr"},
    })
    if response.get("status") != "success":
        raise ValueError("Docling 未完整解析资料，请检查文件是否损坏或加密")
    document = response.get("document", {}).get("json_content")
    if isinstance(document, str):
        document = json.loads(document)
    if not document:
        raise ValueError("Docling 未返回可用正文")
    section = ""
    for item in document.get("texts", []):
        text = item.get("text", "").strip()
        if not text:
            continue
        if item.get("label") in ("title", "section_header"):
            section = text[:120]
        pages = list(dict.fromkeys(p["page_no"] for p in item.get("prov", []) if p.get("page_no")))
        locator = "、".join(f"第 {p} 页" for p in pages)
        locator = " · ".join(filter(None, (locator, section)))
        yield locator or str(item["self_ref"]), text
    for i, table in enumerate(document.get("tables", []), 1):
        page = next((p.get("page_no") for p in table.get("prov", []) if p.get("page_no")), None)
        grid = table.get("data", {}).get("grid", [])
        text = "\n".join(" | ".join(str(c.get("text", "")) for c in row) for row in grid)
        if text.strip():
            yield (f"第 {page} 页 · " if page else "") + f"表 {i}", text


def document_metadata(info):
    region, version, start, end = [str(info.get(k) or "").strip()[:120]
                                   for k in ("region", "version", "start", "end")]
    start, end = [date.fromisoformat(s).isoformat() if s else "" for s in (start, end)]
    if start and end and start > end:
        raise ValueError("生效日期须早于失效日期")
    return region, version, start, end


def import_document(root, db, file, info):
    if file.suffix.lower() not in {".pdf", ".docx", ".txt", ".md"} or file.stat().st_size > 30 << 20:
        raise ValueError("请选择 30 MB 以内的 PDF、DOCX、TXT 或 Markdown")
    content = file.read_bytes()
    key = hashlib.sha256(content).hexdigest()
    if db.execute("SELECT 1 FROM documents WHERE id=?", (key,)).fetchone():
        raise ValueError("这份资料已导入")
    metadata = document_metadata(info)
    name = model(db)
    chunks = list(islice(((loc, text[offset:offset + 900]) for loc, text in parse(file, content)
                          for offset in range(0, len(text), 800)), 25001))
    if not chunks or len(chunks) > 25000:
        raise ValueError("未提取到正文或资料超出索引上限")
    ids = [str(uuid5(NAMESPACE_URL, f"{key}:{i}")) for i in range(len(chunks))]
    col = "/collections/" + collection(root, name)
    for offset in range(0, len(chunks), 32):
        group = chunks[offset:offset + 32]
        vectors = embed(name, [text for _, text in group])
        if offset == 0 and not service("Qdrant", col + "/exists")["result"]["exists"]:
            service("Qdrant", col, {"vectors": {"size": len(vectors[0]), "distance": "Cosine"}}, "PUT")
        points = [{"id": id, "vector": vector, "payload": {"document": key}}
                  for id, vector in zip(ids[offset:offset + 32], vectors, strict=True)]
        service("Qdrant", col + "/points?wait=true", {"points": points}, "PUT")
    with db:
        db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?)", (key, file.name, *metadata, content))
        db.executemany("INSERT INTO passages VALUES (?,?,?,?)", ((id, key, loc, text)
                       for id, (loc, text) in zip(ids, chunks, strict=True)))


def search(root, query, region="", on_date=""):
    if not query.strip() or len(query) > 500:
        raise ValueError("请输入 1–500 字的资料查询")
    on_date = date.fromisoformat(on_date).isoformat() if on_date else date.today().isoformat()
    if not (Path(root) / "knowledge/index.sqlite").is_file():
        return []
    with closing(connect(root)) as db:
        if not db.execute("SELECT 1 FROM documents LIMIT 1").fetchone():
            return []
        name = model(db)
        filters = """WHERE (?='' OR d.region='' OR d.region=?)
                     AND (d.start='' OR d.start<=?) AND (d.end='' OR d.end>=?)"""
        args = (region, region, on_date, on_date)
        sql = """SELECT p.id,p.location,p.text,d.name,d.region,d.version,d.start,d.end
                 FROM passages p JOIN documents d ON p.document=d.id """
        terms = re.findall(r"\w+", query)
        lexical = []
        if expr := " OR ".join(f'"{value}"' for value in terms if len(value) >= 3):
            lexical = db.execute(sql + " JOIN lexical ON lexical.id=p.id " + filters +
                " AND lexical MATCH ? ORDER BY rank LIMIT 24", (*args, expr)).fetchall()
        eligible = [row[0] for row in db.execute("SELECT d.id FROM documents d " + filters, args)]
        if not eligible:
            return []
        vec = embed(name, [query])[0]
        points = service("Qdrant", "/collections/" + collection(root, name) + "/points/query",
                         {"query": vec, "limit": 24, "filter": {"must": [
                             {"key": "document", "match": {"any": eligible}},
                         ]}})["result"]["points"]
        ids = [p["id"] for p in points if p["score"] >= .42]
        matches = {row["id"]: row for row in db.execute(sql + filters + " AND p.id IN (" +
            ",".join("?" for _ in ids) + ")", (*args, *ids))} if ids else {}
        ranks = {}
        records = {}
        for rows in (lexical, [matches[id] for id in ids if id in matches]):
            for rank, row in enumerate(rows, 1):
                id = row["id"]
                ranks[id] = ranks.get(id, 0) + 1 / (60 + rank)
                records[id] = dict(row)
        return [records[id] for id in sorted(ranks, key=ranks.get, reverse=True)[:8]]


def read(root, id):
    if not (Path(root) / "knowledge/index.sqlite").is_file():
        return None
    with closing(connect(root)) as db:
        row = db.execute("""SELECT p.location,p.text,d.name,d.region,d.version,d.start,d.end
                            FROM passages p JOIN documents d ON d.id=p.document WHERE p.id=?""", (id,)).fetchone()
        return dict(row) if row else None


def manage(root, action, value):
    with closing(connect(root)) as db:
        if action in {"model", "import", "delete", "edit"}:
            db.execute("BEGIN IMMEDIATE")
        if action == "model":
            if not isinstance(value, str) or not re.fullmatch(r"[\w./:-]{1,100}", value) or db.execute(
                "SELECT 1 FROM documents LIMIT 1"
            ).fetchone():
                raise ValueError("请填写有效的本机模型名；已有资料时须清空索引后更换模型")
            embed(value, ["向量连接检查"])
            with db:
                db.execute("INSERT OR REPLACE INTO settings VALUES ('model',?)", (value,))
        elif action == "import":
            import_document(root, db, Path(value["file"]), value)
        elif action == "edit":
            with db:
                db.execute("UPDATE documents SET region=?, version=?, start=?, end=? WHERE id=?",
                           (*document_metadata(value), value["id"]))
        elif action == "export":
            row = db.execute("SELECT source FROM documents WHERE id=?", (value["id"],)).fetchone()
            if row is None:
                raise ValueError("资料已删除")
            with Path(value["file"]).open("xb") as file:
                file.write(row[0])
            return {"saved": True}
        elif action == "delete":
            ids = [row[0] for row in db.execute("SELECT id FROM passages WHERE document=?", (value,))]
            if ids:
                service("Qdrant", "/collections/" + collection(root, model(db)) + "/points/delete?wait=true",
                        {"points": ids})
            with db:
                db.execute("DELETE FROM documents WHERE id=?", (value,))
        elif action == "test":
            return search(root, value["query"], value.get("region", ""), value.get("on_date", ""))
        elif action != "list":
            raise ValueError("未知的资料库操作")
        return {"documents": documents(db), "model": next(iter(db.execute(
            "SELECT value FROM settings WHERE name='model'").fetchone() or ("",)))}
