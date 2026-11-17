"""Read and summarize the attribute table of a sealed GeoPackage."""
from contextlib import closing, contextmanager
import sqlite3

import pyogrio

from guizhi.infrastructure.operations.table import identifier


@contextmanager
def attribute_table(file, name):
    info = pyogrio.read_info(file, layer=name)
    fid, fields = info["fid_column"], dict(zip(info["fields"], info["dtypes"], strict=True))
    with closing(sqlite3.connect(file.as_uri() + "?mode=ro", uri=True)) as db:
        yield db, identifier(name), fid, fields


def chosen(db, fids):
    if not isinstance(fids, list) or any(type(fid) is not int for fid in fids):
        raise ValueError("要素编号无效")
    db.execute("CREATE TEMP TABLE selected (id INTEGER PRIMARY KEY)")
    db.executemany("INSERT OR IGNORE INTO selected VALUES (?)", ((value,) for value in fids))


def table_page(file, name, payload):
    page = payload["page"]
    if type(page) is not int or not 0 <= page <= 1_000_000:
        raise ValueError("属性表页码无效")
    with attribute_table(file, name) as (db, table, fid, fields):
        index = identifier(fid)
        columns = ', '.join([index, *map(identifier, fields)])
        order = payload.get("order", [])
        if not isinstance(order, list) or len(order) > 5 or any(
                not isinstance(item, dict) or item.get("field") not in {*fields, fid} or
                item.get("direction") not in {"ascend", "descend"} for item in order):
            raise ValueError("排序字段无效，最多选择五个字段")
        sorting = ", ".join([f'{identifier(item["field"])} {"DESC" if item["direction"] == "descend" else "ASC"}'
                             for item in order] + [index])
        filters = ""
        if payload.get("selection") is not None:
            chosen(db, payload["selection"])
            filters = f" WHERE {index} IN (SELECT id FROM selected)"
        rows = db.execute(f"SELECT {columns} FROM {table}{filters} ORDER BY {sorting} LIMIT 50 OFFSET ?",
                          (page * 50,)).fetchall()
    return {"rows": [{"id": row[0], "values": {field: value.hex() if isinstance(value, bytes) else value
                       for field, value in zip(fields, row[1:], strict=True)}} for row in rows]}


def filter_ids(file, name, where):
    from guizhi.infrastructure.operations.data import AttributeFilter
    from guizhi.infrastructure.operations.sql_expression import attribute_predicate, evaluate

    info = pyogrio.read_info(file, layer=name)
    fid = info["fid_column"]
    fields = dict(zip(info["fields"], info["dtypes"], strict=True))
    predicate, arguments = attribute_predicate(AttributeFilter.model_validate(where), fields)
    rows = evaluate(file, name, fid, fields, predicate, predicate=True, arguments=arguments)
    return sorted(row[0] for row in rows)


def complement(file, name, payload):
    with attribute_table(file, name) as (db, table, fid, _):
        chosen(db, payload["fids"])
        index = identifier(fid)
        return [row[0] for row in db.execute(f"SELECT {index} FROM {table} WHERE {index} NOT IN (SELECT id FROM selected) ORDER BY {index}")]


def statistics(file, name, payload):
    with attribute_table(file, name) as (db, table, fid, fields):
        field = payload.get("field")
        if field not in {*fields, fid}:
            raise ValueError("请选择有效的统计字段")
        column = identifier(field)
        numeric = field == fid or str(fields[field]).startswith(("int", "uint", "float"))
        metrics = f", min({column}), max({column}), sum(1.0 * {column}), avg({column})" if numeric else ""
        filters = ""
        if payload.get("selection") is not None:
            chosen(db, payload["selection"])
            filters = f" WHERE {identifier(fid)} IN (SELECT id FROM selected)"
        row = db.execute(f"SELECT count(*), count({column}), count(DISTINCT {column}){metrics} FROM {table}{filters}").fetchone()
        return dict(zip(("count", "valid", "distinct", "min", "max", "sum", "mean"), row))


def unique_values(file, name, payload):
    field = payload.get("field")
    term = payload.get("query", "")
    page = payload.get("page", 0)
    if not isinstance(term, str) or len(term) > 200 or type(page) is not int or not 0 <= page <= 100_000:
        raise ValueError("唯一值搜索参数无效")
    with attribute_table(file, name) as (db, table, _, fields):
        if not isinstance(field, str) or field not in fields:
            raise ValueError("请选择有效的属性字段")
        column = identifier(field)
        filters = f"WHERE instr(lower(CAST({column} AS TEXT)), lower(?)) > 0 OR ({column} IS NULL AND ?)" if term else ""
        params = (term, int(term.casefold() in {"空值", "null"})) if term else ()
        rows = db.execute(f"SELECT DISTINCT {column} FROM {table} {filters} ORDER BY {column} LIMIT 51 OFFSET ?",
                          (*params, page * 50)).fetchall()
    values = [row[0] for row in rows[:50]]
    if any(isinstance(value, bytes) for value in values):
        raise ValueError("二进制字段不支持获取唯一值")
    return {"values": [str(value) if type(value) is int and abs(value) > 2**53 - 1 else value for value in values],
            "more": len(rows) > 50}


def feature_attributes(file, name, payload):
    feature = payload.get("fid")
    if type(feature) is not int:
        raise ValueError("要素编号无效")
    with attribute_table(file, name) as (db, table, fid, fields):
        index = identifier(fid)
        columns = ', '.join([index, *map(identifier, fields)])
        row = db.execute(f"SELECT {columns} FROM {table} WHERE {index}=?", (feature,)).fetchone()
    if row is None:
        raise ValueError("要素已不存在")
    return {name: value.hex() if isinstance(value, bytes) else str(value) if type(value) is int and abs(value) > 2**53 - 1 else value
            for name, value in zip(fields, row[1:], strict=True)}
