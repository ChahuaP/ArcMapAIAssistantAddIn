"""Evaluate SQLite expressions against one sealed dataset."""
from contextlib import closing
from pathlib import Path
import sqlite3
from time import monotonic

from guizhi.infrastructure.operations.table import identifier

FUNCTIONS = frozenset("""
    abs round min max coalesce ifnull nullif upper lower length trim ltrim rtrim
    replace substr substring instr like glob cast iif typeof unicode char concat
    date datetime strftime julianday unixepoch printf format
""".split())


def evaluate(path: Path, layer, fid, fields, expression, *, predicate=False):
    if not isinstance(expression, str) or not 1 <= len(expression.strip()) <= 4000 or "\0" in expression:
        raise ValueError("请输入不超过 4000 个字符的 SQL 表达式")
    fields = set(fields) | {fid}
    selections = 0

    def authorize(code, first, second, database, _):
        nonlocal selections
        if code == sqlite3.SQLITE_SELECT:
            selections += 1
            allowed = selections == 1
        elif code == sqlite3.SQLITE_READ:
            allowed = database == "main" and first == layer and second in fields
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
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1 << 20)
            deadline = monotonic() + 25
            db.set_progress_handler(lambda: monotonic() > deadline, 10_000)
            db.set_authorizer(authorize)
            return db.execute(sql).fetchall()
    except sqlite3.Error as error:
        raise ValueError(f"SQL 表达式无效或超时：{error}") from error
