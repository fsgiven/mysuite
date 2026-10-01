"""Opt-in job history (SQLite). Off unless asked for (--record-history):
a log of which files you processed, and where the results went, is exactly
the kind of trace a privacy-minded tool shouldn't leave by default."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from mysuite.enhance._paths import history_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input_path TEXT NOT NULL,
    output_path TEXT,
    preset TEXT,
    backend TEXT,
    status TEXT NOT NULL,
    error TEXT,
    input_w INTEGER, input_h INTEGER, output_w INTEGER, output_h INTEGER,
    duration_seconds REAL,
    created_at REAL NOT NULL
);
"""


@dataclass
class HistoryRow:
    id: int
    input_path: str
    output_path: str | None
    preset: str | None
    backend: str | None
    status: str
    error: str | None
    duration_seconds: float | None
    created_at: float


def _connect() -> sqlite3.Connection:
    path = history_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA)
    return conn


def record(
    *, input_path: str, output_path: str | None, preset: str | None, backend: str | None,
    status: str, error: str | None = None, input_size: tuple[int, int] | None = None,
    output_size: tuple[int, int] | None = None, duration_seconds: float | None = None,
) -> None:
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO jobs (input_path, output_path, preset, backend, status, error, "
            "input_w, input_h, output_w, output_h, duration_seconds, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (input_path, output_path, preset, backend, status, error,
             *(input_size or (None, None)), *(output_size or (None, None)),
             duration_seconds, time.time()),
        )
        conn.commit()
    finally:
        conn.close()


def list_jobs(limit: int = 50) -> list[HistoryRow]:
    if not history_db_path().exists():
        return []
    conn = _connect()
    try:
        cur = conn.execute(
            "SELECT id, input_path, output_path, preset, backend, status, error, "
            "duration_seconds, created_at FROM jobs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return [HistoryRow(*row) for row in cur.fetchall()]
    finally:
        conn.close()


def clear() -> int:
    """Deletes the history database. Returns rows removed."""
    path = history_db_path()
    if not path.exists():
        return 0
    count = len(list_jobs(limit=10**9))
    path.unlink()
    return count
