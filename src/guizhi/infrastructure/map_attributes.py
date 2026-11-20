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


def shape_page(file, name, payload):
    """Read a Shapefile attribute page without copying geometries or converting it."""
    import json

    page = payload["page"]
    if type(page) is not int or not 0 <= page <= 1_000_000:
        raise ValueError("属性表页码无效")
    order, selection, focus = payload.get("order", []), payload.get("selection"), payload.get("focusFid")
    if selection is not None and (not isinstance(selection, list) or any(type(fid) is not int for fid in selection)):
        raise ValueError("要素编号无效")
    if focus is not None and (type(focus) is not int or focus < 0):
        raise ValueError("要素编号无效")
    if selection == []:
        return {"page": 0, "rows": []}
    frame = None
    if not order and selection is None:
        candidate = page if focus is None else focus // 50
        trial = pyogrio.read_dataframe(file, layer=name, read_geometry=False, fid_as_index=True,
                                      skip_features=candidate * 50, max_features=50)
        if focus is None or focus in trial.index:
            page, frame = candidate, trial
    if frame is None and not order and selection is not None:
        ids = sorted(set(selection))
        if focus in ids:
            page = ids.index(focus) // 50
        chosen = ids[page * 50:(page + 1) * 50]
        if not chosen:
            return {"page": page, "rows": []}
        frame = pyogrio.read_dataframe(file, layer=name, read_geometry=False, fid_as_index=True, fids=chosen)
    if frame is None:
        # SQL is evaluated by GDAL against the source DBF. Project FID explicitly because
        # a SQL result can have its own row numbering.
        fields = set(pyogrio.read_info(file, layer=name)["fields"])
        if not isinstance(order, list) or len(order) > 5 or any(
            not isinstance(item, dict) or item.get("field") not in {*fields, "fid"} or
            item.get("direction") not in {"ascend", "descend"} for item in order
        ):
            raise ValueError("排序字段无效，最多选择五个字段")
        alias = "guizhi_source_fid"
        while alias in fields:
            alias += "_"
        sorting = ", ".join([f'{("FID" if row["field"] == "fid" else identifier(row["field"]))} '
                             f'{"DESC" if row["direction"] == "descend" else "ASC"}' for row in order] + ["FID"])
        condition = " WHERE FID IN (" + ",".join(map(str, selection)) + ")" if selection is not None else ""
        statement = f"SELECT FID AS {alias}, * FROM {identifier(name)}{condition} ORDER BY {sorting}"
        if focus is not None:
            indexes = pyogrio.read_dataframe(file, read_geometry=False, sql=statement.replace(", *", "", 1),
                                             sql_dialect="OGRSQL")[alias]
            matches = indexes.index[indexes == focus]
            if len(matches):
                page = int(matches[0]) // 50
        frame = pyogrio.read_dataframe(file, read_geometry=False, sql=statement, sql_dialect="OGRSQL",
                                      skip_features=page * 50, max_features=50).set_index(alias)
    safe = frame.map(lambda value: str(value) if type(value) is int and abs(value) > 2**53 - 1 else value)
    values = json.loads(safe.to_json(orient="records", date_format="iso", force_ascii=False))
    return {"page": page, "rows": [{"id": int(fid), "values": row} for fid, row in zip(frame.index, values, strict=True)]}


def shape_attributes(file, name, payload):
    """Run attribute queries against a Shapefile's DBF through GDAL."""
    import json

    action = payload["action"]
    if action == "table":
        return shape_page(file, name, payload)
    if action == "table-feature":
        fid = payload.get("fid")
        if type(fid) is not int or fid < 0:
            raise ValueError("要素编号无效")
        frame = pyogrio.read_dataframe(file, layer=name, fids=[fid], fid_as_index=True)
        if frame.empty:
            raise ValueError("要素已不存在")
        geometry = frame.geometry.iloc[0]
        data = frame.drop(columns=frame.geometry.name)
        safe = data.map(lambda value: str(value) if type(value) is int and abs(value) > 2**53 - 1 else value)
        values = json.loads(safe.to_json(orient="records", date_format="iso", force_ascii=False))
        return {"attributes": values[0], "geometryType": geometry.geom_type if geometry is not None else None}

    table = identifier(name)
    selected = payload.get("selection")
    if selected is not None and (not isinstance(selected, list) or any(type(fid) is not int for fid in selected)):
        raise ValueError("要素编号无效")

    def read(statement):
        return pyogrio.read_dataframe(file, read_geometry=False, sql=statement, sql_dialect="SQLITE")

    if action == "table-complement":
        selected = selected if selected is not None else payload.get("fids")
        if not isinstance(selected, list) or any(type(fid) is not int for fid in selected):
            raise ValueError("要素编号无效")
        condition = " WHERE ROWID NOT IN (" + ",".join(map(str, selected)) + ")" if selected else ""
        return [int(value) for value in read(f"SELECT ROWID AS source_fid FROM {table}{condition} ORDER BY ROWID").iloc[:, 0]]

    info = pyogrio.read_info(file, layer=name)
    fields = dict(zip(info["fields"], info["dtypes"], strict=True))
    field = payload.get("field")
    if not isinstance(field, str) or field not in fields and not (field == "fid" and action == "table-statistics"):
        raise ValueError("请选择有效的属性字段")
    column = "ROWID" if field == "fid" else identifier(field)
    if action == "table-statistics":
        numeric = field == "fid" or str(fields[field]).startswith(("int", "uint", "float"))
        metrics = f", min({column}), max({column}), sum(1.0 * {column}), avg({column})" if numeric else ""
        condition = (" WHERE ROWID IN (" + ",".join(map(str, selected)) + ")") if selected is not None else ""
        result = read(f"SELECT count(*),count({column}),count(DISTINCT {column}){metrics} FROM {table}{condition}")
        return dict(zip(("count", "valid", "distinct", "min", "max", "sum", "mean"),
                        json.loads(result.to_json(orient="values"))[0]))
    term, page = payload.get("query", ""), payload.get("page", 0)
    if not isinstance(term, str) or len(term) > 200 or "\x00" in term or type(page) is not int or not 0 <= page <= 100_000:
        raise ValueError("唯一值搜索参数无效")
    literal = "'" + term.replace("'", "''") + "'"
    condition = (f" WHERE instr(lower(CAST({column} AS TEXT)), lower({literal})) > 0 OR "
                 f"({column} IS NULL AND {int(term.casefold() in {'空值', 'null'})})") if term else ""
    found = read(f"SELECT DISTINCT {column} FROM {table}{condition} ORDER BY {column} LIMIT 51 OFFSET {page * 50}")
    found = found.map(lambda value: str(value) if type(value) is int and abs(value) > 2**53 - 1 else value)
    return {"values": [row[0] for row in json.loads(found.head(50).to_json(orient="values", date_format="iso"))],
            "more": len(found) > 50}


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
        focus = payload.get("focusFid")
        if focus is not None:
            if type(focus) is not int:
                raise ValueError("要素编号无效")
            if order:
                position = db.execute(
                    f"SELECT ordinal FROM (SELECT {index} AS feature_id, row_number() OVER "
                    f"(ORDER BY {sorting}) - 1 AS ordinal FROM {table}{filters}) WHERE feature_id=?", (focus,)
                ).fetchone()
            else:
                conjunction = " AND " if filters else " WHERE "
                position = db.execute(f"SELECT count(*) FROM {table}{filters}{conjunction}{index} < ?", (focus,)).fetchone()
            if position is not None:
                page = position[0] // 50
        rows = db.execute(f"SELECT {columns} FROM {table}{filters} ORDER BY {sorting} LIMIT 50 OFFSET ?",
                          (page * 50,)).fetchall()
    return {"page": page, "rows": [{"id": row[0], "values": {field: value.hex() if isinstance(value, bytes) else value
                       for field, value in zip(fields, row[1:], strict=True)}} for row in rows]}


def filter_ids(file, name, where, *, mode="new_selection", previous=None):
    from guizhi.infrastructure.operations.common import combine_selection
    from guizhi.infrastructure.operations.data import AttributeFilter
    from guizhi.infrastructure.operations.sql_expression import attribute_predicate, evaluate

    info = pyogrio.read_info(file, layer=name)
    fid = info["fid_column"]
    fields = dict(zip(info["fields"], info["dtypes"], strict=True))
    predicate, arguments = attribute_predicate(AttributeFilter.model_validate(where), fields)
    rows = evaluate(file, name, fid, fields, predicate, predicate=True, arguments=arguments)
    old = None
    if previous is not None:
        with attribute_table(file, name) as (db, table, fid, _):
            chosen(db, previous)
            old = {row[0] for row in db.execute(f"SELECT {identifier(fid)} FROM {table} "
                                                f"WHERE {identifier(fid)} IN (SELECT id FROM selected)")}
            if len(old) != len(previous):
                raise ValueError("选区包含失效或重复的要素编号，请重新选择")
    return sorted(combine_selection({row[0] for row in rows}, mode, old))


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
    attributes = {name: value.hex() if isinstance(value, bytes) else str(value) if type(value) is int and abs(value) > 2**53 - 1 else value
            for name, value in zip(fields, row[1:], strict=True)}
    shape = pyogrio.read_dataframe(file, layer=name, fids=[feature], columns=[]).geometry.iloc[0]
    return {"attributes": attributes, "geometryType": shape.geom_type if shape is not None else None}
