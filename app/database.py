"""SQLite 持久层。

一个演出季对应一行 seasons，每次成功交单生成一个新批次（batches 行），
该批次的单据整体写入 orders，核算结论写入 daily_bounds。
查询永远只读已生效（最新）批次；交单失败时旧批次保持不动。
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Iterator, Optional

DB_PATH = os.getenv("TICKET_DB_PATH", "/data/tickets.db")

_SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS seasons (
    season_id   TEXT PRIMARY KEY,
    start_date  TEXT NOT NULL,
    end_date    TEXT NOT NULL,
    n_days      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id   TEXT NOT NULL REFERENCES seasons(season_id),
    batch_no    INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    feasible    INTEGER NOT NULL,
    UNIQUE(season_id, batch_no)
);

CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id   TEXT NOT NULL,
    batch_no    INTEGER NOT NULL,
    order_no    TEXT NOT NULL,
    l_idx       INTEGER NOT NULL,
    r_idx       INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    UNIQUE(season_id, batch_no, order_no)
);

CREATE TABLE IF NOT EXISTS daily_bounds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id   TEXT NOT NULL,
    batch_no    INTEGER NOT NULL,
    day_index   INTEGER NOT NULL,
    min_tickets INTEGER NOT NULL,
    max_tickets INTEGER,          -- NULL 表示无上限
    UNIQUE(season_id, batch_no, day_index)
);

CREATE TABLE IF NOT EXISTS contradictions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    season_id   TEXT NOT NULL,
    batch_no    INTEGER NOT NULL,
    order_no    TEXT NOT NULL
);
"""


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_db(db_path: Optional[str] = None) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(_SCHEMA)
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """显式事务（BEGIN IMMEDIATE 立刻拿写锁）。"""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_season(conn: sqlite3.Connection, season_id: str) -> Optional[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM seasons WHERE season_id = ?", (season_id,)
    )
    return cur.fetchone()


def current_batch_no(conn: sqlite3.Connection, season_id: str) -> int:
    cur = conn.execute(
        "SELECT COALESCE(MAX(batch_no), 0) AS b FROM batches WHERE season_id = ?",
        (season_id,),
    )
    return int(cur.fetchone()["b"])


def insert_season(
    conn: sqlite3.Connection,
    season_id: str,
    start_date: date,
    end_date: date,
    n_days: int,
) -> None:
    conn.execute(
        "INSERT INTO seasons(season_id, start_date, end_date, n_days) "
        "VALUES (?, ?, ?, ?)",
        (season_id, start_date.isoformat(), end_date.isoformat(), n_days),
    )


def insert_batch(
    conn: sqlite3.Connection,
    season_id: str,
    batch_no: int,
    feasible: bool,
) -> None:
    conn.execute(
        "INSERT INTO batches(season_id, batch_no, feasible) VALUES (?, ?, ?)",
        (season_id, batch_no, 1 if feasible else 0),
    )


def insert_orders(
    conn: sqlite3.Connection,
    season_id: str,
    batch_no: int,
    rows: list[tuple[str, int, int, int]],
) -> None:
    conn.executemany(
        "INSERT INTO orders(season_id, batch_no, order_no, l_idx, r_idx, total) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(season_id, batch_no, no, l, r, t) for no, l, r, t in rows],
    )


def insert_daily_bounds(
    conn: sqlite3.Connection,
    season_id: str,
    batch_no: int,
    rows: list[tuple[int, int, Optional[int]]],
) -> None:
    conn.executemany(
        "INSERT INTO daily_bounds(season_id, batch_no, day_index, min_tickets, max_tickets) "
        "VALUES (?, ?, ?, ?, ?)",
        [(season_id, batch_no, idx, lo, hi) for idx, lo, hi in rows],
    )


def insert_contradictions(
    conn: sqlite3.Connection,
    season_id: str,
    batch_no: int,
    order_nos: list[str],
) -> None:
    conn.executemany(
        "INSERT INTO contradictions(season_id, batch_no, order_no) VALUES (?, ?, ?)",
        [(season_id, batch_no, no) for no in order_nos],
    )


def load_orders(
    conn: sqlite3.Connection, season_id: str, batch_no: int
) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM orders WHERE season_id = ? AND batch_no = ? ORDER BY id",
        (season_id, batch_no),
    )
    return cur.fetchall()


def load_daily_bounds(
    conn: sqlite3.Connection, season_id: str, batch_no: int
) -> list[sqlite3.Row]:
    cur = conn.execute(
        "SELECT * FROM daily_bounds WHERE season_id = ? AND batch_no = ? "
        "ORDER BY day_index",
        (season_id, batch_no),
    )
    return cur.fetchall()


def load_contradictions(
    conn: sqlite3.Connection, season_id: str, batch_no: int
) -> list[str]:
    cur = conn.execute(
        "SELECT order_no FROM contradictions WHERE season_id = ? AND batch_no = ?",
        (season_id, batch_no),
    )
    return [r["order_no"] for r in cur.fetchall()]
