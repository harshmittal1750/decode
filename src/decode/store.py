"""The only module that touches the database.

Four tables mirror the four states of the pipeline, so every run is debuggable
after the fact:

    runs       one row per collection run, with wall time and outcome
    raw        the exact bytes each endpoint returned, gzipped   (expires)
    processed  the reduced rows the analyses read                (forever)
    errors     every failure, with traceback and the attempt count

SQLite rather than JSONL for three specific reasons:
  * commits are atomic, so two overlapping cron runs cannot splice a row --
    with 10KB JSONL lines, appends exceed the atomic-write size and interleave
  * "show me every heatmap error last week" is a query, not a scan
  * raw / processed / errors are separate lifetimes; one file, three policies

Alignment note: funding and basis timestamps are offset by one bar. That fix
lives in aligned() -- a READER -- so the archive keeps exactly what the API
sent and a better fix later can be re-run across all history.
"""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import DB_PATH, RAW_RETENTION_DAYS

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    ok_count    INTEGER DEFAULT 0,
    err_count   INTEGER DEFAULT 0,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS raw (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    stream      TEXT    NOT NULL,
    fetched_at  INTEGER NOT NULL,
    url         TEXT    NOT NULL,
    status      INTEGER,
    elapsed_ms  INTEGER,
    attempts    INTEGER,
    encrypted   INTEGER,
    body_gz     BLOB    NOT NULL,
    PRIMARY KEY (run_id, stream)
);
CREATE TABLE IF NOT EXISTS processed (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    stream      TEXT    NOT NULL,
    fetched_at  INTEGER NOT NULL,
    data        TEXT    NOT NULL,
    PRIMARY KEY (run_id, stream)
);
CREATE TABLE IF NOT EXISTS errors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER REFERENCES runs(run_id) ON DELETE CASCADE,
    stream      TEXT    NOT NULL,
    occurred_at INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    message     TEXT    NOT NULL,
    traceback   TEXT
);
CREATE INDEX IF NOT EXISTS ix_processed_stream ON processed(stream, fetched_at);
CREATE INDEX IF NOT EXISTS ix_errors_stream    ON errors(stream, occurred_at);
CREATE INDEX IF NOT EXISTS ix_raw_fetched      ON raw(fetched_at);
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")      # concurrent readers during a run
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def now_ms() -> int:
    return int(time.time() * 1000)


# --- writers -----------------------------------------------------------------

def start_run(conn: sqlite3.Connection, note: str = "") -> int:
    cur = conn.execute("INSERT INTO runs (started_at, note) VALUES (?, ?)", (now_ms(), note))
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, ok: int, err: int) -> None:
    conn.execute("UPDATE runs SET finished_at=?, ok_count=?, err_count=? WHERE run_id=?",
                 (now_ms(), ok, err, run_id))


def save_raw(conn, run_id: int, stream: str, fetched) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO raw "
        "(run_id, stream, fetched_at, url, status, elapsed_ms, attempts, encrypted, body_gz)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, stream, now_ms(), fetched.url, fetched.status, fetched.elapsed_ms,
         fetched.attempts, int(fetched.encrypted), fetched.raw_gz))


def save_processed(conn, run_id: int, stream: str, data: Any) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO processed (run_id, stream, fetched_at, data) VALUES (?,?,?,?)",
        (run_id, stream, now_ms(), json.dumps(data, separators=(",", ":"))))


def save_error(conn, run_id: int | None, stream: str, kind: str, message: str,
               tb: str = "") -> None:
    conn.execute(
        "INSERT INTO errors (run_id, stream, occurred_at, kind, message, traceback)"
        " VALUES (?,?,?,?,?,?)",
        (run_id, stream, now_ms(), kind, message[:2000], tb[:8000]))


# --- readers -----------------------------------------------------------------

def series(conn, stream: str, limit: int | None = None) -> list[dict]:
    """Every processed row for one stream, oldest first."""
    sql = "SELECT run_id, fetched_at, data FROM processed WHERE stream=? ORDER BY fetched_at"
    if limit:
        sql = (f"SELECT * FROM ({sql} DESC LIMIT {int(limit)}) ORDER BY fetched_at")
    return [{"run_id": r["run_id"], "fetched_at": r["fetched_at"], **json.loads(r["data"])}
            for r in conn.execute(sql, (stream,))]


def latest(conn, stream: str) -> dict | None:
    rows = conn.execute(
        "SELECT run_id, fetched_at, data FROM processed WHERE stream=?"
        " ORDER BY fetched_at DESC LIMIT 1", (stream,)).fetchall()
    if not rows:
        return None
    return {"run_id": rows[0]["run_id"], "fetched_at": rows[0]["fetched_at"],
            **json.loads(rows[0]["data"])}


def raw_body(conn, run_id: int, stream: str) -> bytes | None:
    """The exact bytes the API returned -- for debugging a broken reducer."""
    import gzip
    row = conn.execute("SELECT body_gz FROM raw WHERE run_id=? AND stream=?",
                       (run_id, stream)).fetchone()
    return gzip.decompress(row["body_gz"]) if row else None


def recent_errors(conn, limit: int = 20, stream: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT * FROM errors"
    args: tuple = ()
    if stream:
        sql += " WHERE stream=?"
        args = (stream,)
    return conn.execute(sql + " ORDER BY occurred_at DESC LIMIT ?", (*args, limit)).fetchall()


BAR_MS = 8 * 3600 * 1000
MAX_PLAUSIBLE_BASIS_SD = 0.3      # a real perp-spot basis is tenths of a percent


def aligned(conn, max_sd: float = MAX_PLAUSIBLE_BASIS_SD) -> list[dict]:
    """funding + basis joined on the SAME bar of the 8h grid.

    CoinGlass stamps these two feeds one bar apart. Joining them naively makes
    "basis" equal the NEXT bar's return, which manufactures a correlation of
    +0.998 -- it looks like a stunning result and is pure artifact.

    Rather than hardcode the offset (it is a CoinGlass convention that could
    change), try each shift and keep the one whose basis has a plausible
    magnitude. A price change has sd ~1.2%; a real basis ~0.07%. If no shift
    produces a plausible series we return nothing, because a wrong join here
    fails silently downstream -- it returns numbers, just false ones.

    Both series come from the newest row of each stream: every call to these
    endpoints returns its own history, so the tail is complete and consistent.
    """
    import statistics as st

    f_row, b_row = latest(conn, "funding"), latest(conn, "basis")
    if not f_row or not b_row:
        return []
    spot_at = {int(ts): float(v) for ts, v in f_row.get("series", []) if v}
    perp_ser = [(int(ts), float(v)) for ts, v in b_row.get("series", []) if v]
    if len(spot_at) < 4 or len(perp_ser) < 4:
        return []

    best = None
    for shift in (-2, -1, 0, 1, 2):
        pairs = [(ts, p, spot_at[ts + shift * BAR_MS])
                 for ts, p in perp_ser if ts + shift * BAR_MS in spot_at]
        if len(pairs) < 4:
            continue
        sd = st.stdev([(p / s - 1) * 100 for _, p, s in pairs])
        if best is None or sd < best[0]:
            best = (sd, shift, pairs)

    if best is None or best[0] > max_sd:
        return []                      # no plausible join; refuse rather than lie

    _, shift, pairs = best
    rates = f_row.get("rates", {})
    return [{"ts": ts, "perp": p, "spot": s, "shift": shift,
             "basis_pct": (p / s - 1) * 100, "rates": rates}
            for ts, p, s in sorted(pairs)]


# --- maintenance -------------------------------------------------------------

def sweep_raw(conn, days: int = RAW_RETENTION_DAYS) -> int:
    """Drop raw blobs past the debugging window.

    Only ever touches `raw`. The processed table holds the heatmap live book,
    which cannot be re-fetched from anywhere, so nothing expires it.
    """
    cutoff = now_ms() - days * 86_400_000
    cur = conn.execute("DELETE FROM raw WHERE fetched_at < ?", (cutoff,))
    return cur.rowcount


def stats(conn) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "runs": q("SELECT COUNT(*) FROM runs"),
        "processed_rows": q("SELECT COUNT(*) FROM processed"),
        "raw_rows": q("SELECT COUNT(*) FROM raw"),
        "errors": q("SELECT COUNT(*) FROM errors"),
        "streams": [r[0] for r in conn.execute(
            "SELECT DISTINCT stream FROM processed ORDER BY stream")],
        "first_run": q("SELECT MIN(started_at) FROM runs"),
        "last_run": q("SELECT MAX(started_at) FROM runs"),
    }
