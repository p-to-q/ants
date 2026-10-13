"""Read-only SQL over a ledger, for people and agents who want to ask something the other commands do not. Standard library only.

Safety: the file is opened `mode=ro`, `query_only` is on, an authorizer allows nothing but SELECT (reading columns and calling
functions), and a progress handler stops a runaway query after a few seconds. Row count is capped. Nothing here can write.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

MAX_ROWS = 1000
MAX_SECONDS = 10.0

_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, getattr(sqlite3, "SQLITE_RECURSIVE", 33)}


class SqlRefused(ValueError):
    pass


def _authorizer(action, *_):
    return sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY


def _deny_authorizer(deny, deny_columns=()):
    names = {d.lower() for d in deny}
    cols = {(t.lower(), c.lower()) for t, c in deny_columns}

    def auth(action, arg1, arg2, dbname, source):
        if action == sqlite3.SQLITE_READ and isinstance(arg1, str):
            if arg1.lower() in names:
                return sqlite3.SQLITE_DENY             # whole tables a caller may not read at all (an agent never reads sealed-look records)
            if isinstance(arg2, str) and (arg1.lower(), arg2.lower()) in cols:
                return sqlite3.SQLITE_IGNORE           # a column that reads as NULL (file paths)
        return _authorizer(action)
    return auth


def connect_readonly(path, deny=(), deny_columns=()) -> sqlite3.Connection:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"no ledger at {p}")
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=10)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
    except sqlite3.OperationalError as e:            # a directory we cannot write beside, and no write-ahead log to read: the file is at rest
        if "locked" in str(e):
            raise                                    # a writer holds it: reading it as if it were at rest could show a torn file
        conn = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.set_authorizer(_deny_authorizer(deny, deny_columns) if (deny or deny_columns) else _authorizer)
    for lim, val in ((sqlite3.SQLITE_LIMIT_LENGTH, 1 << 20), (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20_000), (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100),
                     (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 20)):
        conn.setlimit(lim, val)                      # a value, a statement or an expression can not grow past a megabyte or so (randomblob(1e9) fails)
    return conn


def run_query(path, sql: str, limit: int = MAX_ROWS, deny=(), deny_columns=()) -> tuple[list[str], list[list]]:
    """(column names, rows) for one SELECT statement. Raises SqlRefused for anything else."""
    text = sql.strip().rstrip(";").strip()
    if not text or ";" in text:
        raise SqlRefused("exactly one statement, please")
    if text.split(None, 1)[0].lower() not in ("select", "with"):
        raise SqlRefused("only SELECT (or WITH ... SELECT) is allowed")
    conn = connect_readonly(path, deny, deny_columns)
    deadline = time.monotonic() + MAX_SECONDS
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        try:
            cur = conn.execute(text)
            cols = [d[0] for d in cur.description or []]
            rows = [list(r) for r in cur.fetchmany(min(limit, MAX_ROWS))]       # rows are produced here: the time limit can interrupt this too
        except sqlite3.DatabaseError as e:
            raise SqlRefused(f"{e}") from e
        return cols, rows
    finally:
        conn.close()


def schema(path) -> str:
    conn = connect_readonly(path)
    try:
        return "\n\n".join(r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type DESC, name"))
    finally:
        conn.close()
