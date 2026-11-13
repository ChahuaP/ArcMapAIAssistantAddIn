"""Work on a private GeoPackage copy so field types and feature IDs survive edits."""
from contextlib import closing
from datetime import datetime
import json
from math import isfinite
import re
import shutil
import sqlite3
from typing import Any, Literal
from uuid import UUID

import pyogrio
import shapely
from pydantic import BaseModel, ConfigDict, Field

from guizhi.infrastructure.operations.common import condition

FIELD_TYPES = {"smallinteger": "SMALLINT", "integer": "MEDIUMINT", "single": "FLOAT",
               "double": "DOUBLE", "string": "TEXT", "date": "DATETIME", "guid": "TEXT", "blob": "BLOB"}


class FieldSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=255)
    type: Literal["smallinteger", "integer", "single", "double", "string", "date", "guid", "blob"]
    nullable: bool = True
    length: int | None = Field(default=None, gt=0)
    precision: int | None = Field(default=None, ge=0)
    scale: int | None = Field(default=None, ge=0)
    domain: list[Any] | None = None
    default: Any = None


def identifier(value):
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("Invalid field or table name")
    return '"' + value.replace('"', '""') + '"'


def storage_value(spec, value):
    if value is None:
        if not spec.nullable:
            raise ValueError(f"{spec.name} does not allow NULL")
        return None
    kind = spec.type
    if spec.domain is not None and value not in spec.domain:
        raise ValueError(f"{spec.name} is outside its domain")
    if kind in {"integer", "smallinteger"}:
        bits = 16 if kind == "smallinteger" else 32
        if type(value) is not int or not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
            raise ValueError(f"{spec.name} requires a signed {bits}-bit integer")
    elif kind in {"single", "double"}:
        if type(value) not in {int, float} or not isfinite(value):
            raise ValueError(f"{spec.name} requires a finite number")
        if kind == "single" and abs(value) > 3.4028235e38:
            raise ValueError(f"{spec.name} exceeds the single-precision range")
        if spec.scale is not None and value != round(value, spec.scale):
            raise ValueError(f"{spec.name} exceeds its scale")
        if spec.precision is not None:
            from decimal import Decimal

            digits = Decimal(str(value)).normalize().as_tuple()
            if len(digits.digits) + max(0, digits.exponent) > spec.precision:
                raise ValueError(f"{spec.name} exceeds its precision")
    elif kind == "blob":
        if not isinstance(value, str) or len(value) % 2 or not re.fullmatch(r"[0-9a-fA-F]*", value):
            raise ValueError(f"{spec.name} requires a hexadecimal BLOB string")
        value = bytes.fromhex(value)
    elif not isinstance(value, str):
        raise ValueError(f"{spec.name} requires text")
    elif kind == "guid":
        value = str(UUID(value))
    elif kind == "date":
        value = datetime.fromisoformat(value).isoformat(timespec="milliseconds")
    elif spec.length is not None and len(value) > spec.length:
        raise ValueError(f"{spec.name} exceeds its text length")
    return value


def install_spatial_functions(db):
    # GeoPackage RTree triggers resolve these functions even for attribute-only updates.
    def geometry(value):
        if value is None:
            return None
        envelopes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
        if len(value) < 8 or value[:2] != b"GP" or value[2] != 0:
            raise ValueError("Invalid GeoPackage geometry header")
        return shapely.from_wkb(value[8 + envelopes[(value[3] >> 1) & 7]:])

    def empty(value):
        shape = geometry(value)
        return None if shape is None else int(shape.is_empty)

    def bound(value, axis):
        shape = geometry(value)
        return None if shape is None or shape.is_empty else shape.bounds[axis]

    db.create_function("ST_IsEmpty", 1, empty, deterministic=True)
    for index, name in enumerate(("ST_MinX", "ST_MinY", "ST_MaxX", "ST_MaxY")):
        db.create_function(name, 1, lambda value, axis=index: bound(value, axis), deterministic=True)


def table_copy(ctx, action):
    from guizhi.infrastructure.operations.runner import data_revision

    source = ctx.directory / ctx.evidence[0]["path"]
    output = ctx.path("result.gpkg")
    shutil.copyfile(source, output)
    layer = ctx.request.inputs[0].layer
    with closing(sqlite3.connect(output)) as db, db:
        install_spatial_functions(db)
        db.execute("PRAGMA foreign_keys=ON")
        table = identifier(layer)
        fields = {row[1]: row for row in db.execute(f"PRAGMA table_info({table})")}
        change = action(db, table, fields)
        db.execute("UPDATE gpkg_contents SET last_change = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE table_name=?", (layer,))
    frame = pyogrio.read_dataframe(output, layer=layer, columns=[], fid_as_index=True)
    original = ctx.frames[0].loc[frame.index]
    if original.crs != frame.crs or shapely.to_wkb(original.geometry).tolist() != shapely.to_wkb(frame.geometry).tolist():
        raise ValueError("Attribute edit changed feature geometries")
    count = len(ctx.frames[0]) - change.get("deleted", 0)
    if len(frame) != count:
        raise ValueError("Attribute edit changed unexpected rows")
    ctx.record("export", output)
    return {"export_layer": layer, "data_revision": data_revision(output), "statistics": {"feature_count": count},
            "changeset": {"target_revision": ctx.selection_hashes[0], **change}}


def add_field(ctx, field: FieldSpec):
    def action(db, table, fields):
        if field.name.casefold() in {name.casefold() for name in fields}:
            raise ValueError("Field already exists")
        if field.length is not None and field.type != "string":
            raise ValueError("Only text fields have a length")
        if (field.precision is not None or field.scale is not None) and field.type not in {"single", "double"}:
            raise ValueError("Only floating-point fields have precision and scale")
        if field.precision is not None and field.scale is not None and field.scale > field.precision:
            raise ValueError("Field scale exceeds precision")
        if field.domain is not None:
            for item in field.domain:
                storage_value(field, item)
        value = storage_value(field, field.default)
        column = identifier(field.name)
        default = db.execute("SELECT quote(?)", (value,)).fetchone()[0]
        constraints = (" NOT NULL" if not field.nullable else "") + f" DEFAULT {default}"
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {FIELD_TYPES[field.type]}{constraints}")
        db.execute("""CREATE TABLE IF NOT EXISTS guizhi_field_contract (
            layer TEXT, field TEXT, body TEXT NOT NULL, PRIMARY KEY (layer, field))""")
        db.execute("INSERT INTO guizhi_field_contract VALUES (?, ?, ?)",
                   (ctx.request.inputs[0].layer, field.name, field.model_dump_json()))
        stored = db.execute(f"SELECT DISTINCT {column} FROM {table}").fetchall()
        if stored not in ([], [(value,)]):
            raise ValueError("Field default did not survive export")
        return {"added_field": field.name, "field": field.model_dump(mode="json")}

    return table_copy(ctx, action)


def attributes(ctx, fields):
    return {name: row for name, row in fields.items()
            if not row[5] and name != ctx.info[0]["geometry_name"]}


def delete_field(ctx, field: str):
    def action(db, table, fields):
        if field not in attributes(ctx, fields):
            raise ValueError("Only existing attribute fields can be deleted")
        db.execute(f"ALTER TABLE {table} DROP COLUMN {identifier(field)}")
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='guizhi_field_contract'").fetchone():
            db.execute("DELETE FROM guizhi_field_contract WHERE layer=? AND field=?",
                       (ctx.request.inputs[0].layer, field))
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gpkg_data_columns'").fetchone():
            db.execute("DELETE FROM gpkg_data_columns WHERE table_name=? AND column_name=?",
                       (ctx.request.inputs[0].layer, field))
        return {"deleted_field": field}

    return table_copy(ctx, action)


def calculate_field(ctx, field: str, source_field: str, operator: Literal["copy", "add", "subtract", "multiply", "divide"] = "copy",
                    operand: float = 0):
    frame = ctx.frames[0]
    if any(name not in frame or name == frame.geometry.name for name in (field, source_field)):
        raise ValueError("请选择有效的属性字段")
    import operator as arithmetic
    from pandas.api.types import is_numeric_dtype

    values = frame[source_field]
    if operator != "copy":
        if not is_numeric_dtype(values) or not isfinite(operand) or (operator == "divide" and operand == 0):
            raise ValueError("算术计算需要数字字段和有效数值，除数不能为零")
        compute = {"add": arithmetic.add, "subtract": arithmetic.sub,
                   "multiply": arithmetic.mul, "divide": arithmetic.truediv}
        values = compute[operator](values, operand)
    from pandas import isna

    def action(db, table, fields):
        column = identifier(field)
        fid = identifier(ctx.info[0]["fid_column"])
        data = []
        for key, value in values.items():
            if isna(value):
                data.append((None, int(key)))
            elif hasattr(value, "item"):
                data.append((value.item(), int(key)))
            else:
                data.append((value, int(key)))
        db.executemany(f"UPDATE {table} SET {column}=? WHERE {fid}=?", data)
        if any(db.execute(f"SELECT {column} FROM {table} WHERE {fid}=?", (key,)).fetchone()[0] != value
               for value, key in data):
            raise ValueError("计算结果与目标字段类型不匹配")
        return {"calculated_field": field, "updated": len(data)}

    return table_copy(ctx, action)


def write_rows(ctx, where, assignments):
    frame = ctx.frames[0]
    fids = list(map(int, frame.index[condition(frame, where)]))
    key = identifier(ctx.info[0]["fid_column"])

    def action(db, table, fields):
        if assignments is None:
            db.executemany(f"DELETE FROM {table} WHERE {key}=?", ((fid,) for fid in fids))
            if any(db.execute(f"SELECT 1 FROM {table} WHERE {key}=?", (fid,)).fetchone() for fid in fids):
                raise ValueError("Deleted rows remain present")
            # The prior extent could have included deleted features. Derive the new extent explicitly.
            remaining = frame.loc[~frame.index.isin(fids)]
            bounds = [None] * 4 if remaining.empty or remaining.geometry.is_empty.all() else remaining.total_bounds.tolist()
            db.execute("UPDATE gpkg_contents SET min_x=?, min_y=?, max_x=?, max_y=? WHERE table_name=?",
                       (*bounds, ctx.request.inputs[0].layer))
            return {"deleted": len(fids), "source_fids": fids}
        available = attributes(ctx, fields)
        if not assignments or not set(assignments) <= set(available):
            raise ValueError("Assignments must name existing attribute fields")
        values = []
        for name, value in assignments.items():
            row = available[name]
            meta = None
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='guizhi_field_contract'").fetchone():
                meta = db.execute("SELECT body FROM guizhi_field_contract WHERE layer=? AND field=?",
                                  (ctx.request.inputs[0].layer, name)).fetchone()
            if meta:
                spec = FieldSpec.model_validate_json(meta[0])
            else:
                sqltype = row[2].upper().split("(")[0]
                kinds = {**{v: k for k, v in FIELD_TYPES.items()}, "TEXT": "string", "DATE": "date",
                         "INTEGER": "integer", "REAL": "double", "TINYINT": "smallinteger"}
                if sqltype == "BOOLEAN":
                    if value is not None and type(value) is not bool:
                        raise ValueError(f"{name} requires a boolean")
                    values.append(None if value is None else int(value))
                    continue
                if sqltype == "INTEGER" and type(value) is int and -(2**63) <= value < 2**63:
                    values.append(value)
                    continue
                if sqltype not in kinds:
                    raise ValueError(f"Unsupported field storage type: {sqltype}")
                spec = FieldSpec(name=name, type=kinds[sqltype], nullable=not row[3])
            values.append(storage_value(spec, value))
        names = [identifier(name) for name in assignments]
        setters = ",".join(name + "=?" for name in names)
        db.executemany(f"UPDATE {table} SET {setters} WHERE {key}=?", ((*values, fid) for fid in fids))
        if any(db.execute(f"SELECT {','.join(names)} FROM {table} WHERE {key}=?", (fid,)).fetchone() != tuple(values)
               for fid in fids):
            raise ValueError("Updated values did not survive storage")
        return {"updated": len(fids), "source_fids": fids, "fields": list(assignments)}

    return table_copy(ctx, action)


def update_rows(ctx, where: dict, assignments: dict):
    return write_rows(ctx, where, assignments)


def delete_rows(ctx, where: dict):
    return write_rows(ctx, where, None)


def add_coordinates(ctx, x_field: str = "POINT_X", y_field: str = "POINT_Y"):
    frame = ctx.frames[0]
    if not frame.geom_type.eq("Point").all() or frame.geometry.is_empty.any():
        raise ValueError("XY fields require nonempty point geometries")
    names = {column.casefold() for column in frame.columns}
    if x_field.casefold() == y_field.casefold() or {x_field.casefold(), y_field.casefold()} & names:
        raise ValueError("XY fields must have unused distinct names")
    ctx.publish(frame.assign(**{x_field: frame.geometry.x, y_field: frame.geometry.y}),
                geometry_type="Point" if frame.empty else None)
    return {"coordinate_fields": [x_field, y_field], "crs": frame.crs.to_string()}
