"""Evaluate SQLite expressions against one sealed dataset."""
from contextlib import closing
from math import isfinite
from pathlib import Path
import re
import sqlite3
from time import monotonic

from guizhi.infrastructure.operations.table import identifier

FUNCTIONS = frozenset("""
    abs round min max coalesce ifnull nullif upper lower length trim ltrim rtrim
    replace substr substring instr like glob cast iif typeof unicode char concat
    date datetime strftime julianday unixepoch printf format
""".split())


def evaluate(path: Path, layer, fid, fields, expression, *, predicate=False, arguments=None):
    if arguments is None and (not isinstance(expression, str) or not 1 <= len(expression.strip()) <= 4000 or "\0" in expression):
        raise ValueError("请输入不超过 4000 个字符的 SQL 表达式")
    fields = set(fields) | {fid}
    selections = 0

    def authorize(code, first, second, database, _):
        nonlocal selections
        if code == sqlite3.SQLITE_SELECT:
            selections += 1
            allowed = selections == 1
        elif code == sqlite3.SQLITE_READ:
            # SQLite requests a columnless read for expressions that only use a literal or row ID.
            allowed = first == layer and ((database == "main" and second in fields) or (database is None and second == ""))
        elif code == sqlite3.SQLITE_FUNCTION:
            allowed = second.casefold() in FUNCTIONS
        else:
            allowed = False
        return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

    relation, index = identifier(layer), identifier(fid)
    sql = (f"SELECT {index} FROM {relation} WHERE ({expression})" if predicate else
           f"SELECT {index}, ({expression}) FROM {relation}")
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            if arguments is not None:
                db.execute("PRAGMA case_sensitive_like=ON")
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1 << 20)
            deadline = monotonic() + 25
            db.set_progress_handler(lambda: monotonic() > deadline, 10_000)
            db.set_authorizer(authorize)
            return db.execute(sql, () if arguments is None else arguments).fetchall()
    except sqlite3.Error as error:
        if arguments is not None:
            raise ValueError("属性查询执行失败或超时，请检查图层和匹配值") from error
        raise ValueError(f"SQL 表达式无效或超时：{error}") from error


COMPARISONS = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def attribute_value(field, kind, value):
    if str(kind).startswith(("int", "uint")) and isinstance(value, str):
        if not re.fullmatch(r"[+-]?\d{1,20}", value):
            raise ValueError(f"「{field}」需要整数")
        value = int(value)
    if (isinstance(value, int) and not -(2**63) <= value < 2**63 or
            isinstance(value, float) and not isfinite(value)):
        raise ValueError(f"「{field}」的数值超出范围")
    if type(value) not in (str, int, float, bool):
        raise ValueError(f"请为「{field}」填写有效的值")
    return value


def attribute_predicate(where, fields):
    arguments = []

    def column(name):
        if name not in fields:
            raise ValueError(f"图层中没有字段「{name or '未选择'}」")
        return identifier(name)

    def parameter(field, value):
        arguments.append(attribute_value(field, fields[field], value))
        return "?"

    def expression(clause, depth=0):
        if depth >= 16:
            raise ValueError("查询条件嵌套过多")
        op, field = clause.op, clause.field
        keys = {key for key in ("value", "value_field", "values", "conditions", "condition")
                if getattr(clause, key) is not None}
        if field is None and op in {"and", "or"} and keys == {"conditions"}:
            return "(" + (" AND " if op == "and" else " OR ").join(
                expression(item, depth + 1) for item in clause.conditions) + ")"
        if field is None and op == "not" and keys == {"condition"}:
            return "(NOT " + expression(clause.condition, depth + 1) + ")"
        left = column(field)
        if op in {"is_null", "is_not_null"} and not keys:
            return left + (" IS NULL" if op == "is_null" else " IS NOT NULL")
        if op in COMPARISONS and keys in ({"value"}, {"value_field"}):
            right = column(clause.value_field) if clause.value_field else parameter(field, clause.value)
            return left + " " + COMPARISONS[op] + " " + right
        if op in {"contains", "like"} and keys == {"value"} and isinstance(clause.value, str):
            right = parameter(field, clause.value)
            return f"instr({left}, {right}) > 0" if op == "contains" else f"{left} LIKE {right}"
        if op == "in" and keys == {"values"} and len(clause.values) <= 500:
            params = ", ".join(parameter(field, value) for value in clause.values if value is not None)
            return f"{left} IN ({params})" if params else "0"
        if op == "between" and keys == {"values"} and len(clause.values) == 2:
            low, high = (parameter(field, value) for value in clause.values)
            return f"{left} BETWEEN {low} AND {high}"
        raise ValueError("查询条件未填完整或类型不匹配")

    return expression(where), arguments
