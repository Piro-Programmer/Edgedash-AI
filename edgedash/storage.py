"""
storage.py — the ONLY module permitted to import sqlite3 or psycopg2.

Exposes a thin interface over the database. Supports both local SQLite
and hosted Postgres (read from DATABASE_URL).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Any
from dotenv import load_dotenv

load_dotenv()
_POSTGRES_URL = os.environ.get("DATABASE_URL")

if _POSTGRES_URL:
    print("[storage] backend: postgres")
    import psycopg2
    from psycopg2.extras import RealDictCursor
else:
    print("[storage] backend: sqlite  (set DATABASE_URL to use Postgres)")
    import sqlite3

# ---------------------------------------------------------------------------
# Schema DDL Helpers
# ---------------------------------------------------------------------------

def _ddl_listings() -> str:
    return """
CREATE TABLE IF NOT EXISTS listings (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    company     TEXT NOT NULL,
    location    TEXT NOT NULL,
    url         TEXT NOT NULL,
    description TEXT,
    source      TEXT NOT NULL,
    posted_at   TEXT,
    fetched_at  TEXT NOT NULL,
    fit_score   INTEGER,
    fit_reason  TEXT,
    scored_at   TEXT
)
"""

def _ddl_skill_gaps() -> str:
    return """
CREATE TABLE IF NOT EXISTS skill_gaps (
    skill       TEXT PRIMARY KEY,
    frequency   INTEGER NOT NULL DEFAULT 1,
    last_seen   TEXT NOT NULL
)
"""

def _ddl_cycle_log() -> str:
    pk = "SERIAL PRIMARY KEY" if _POSTGRES_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return f"""
CREATE TABLE IF NOT EXISTS cycle_log (
    id              {pk},
    agent           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    records_touched INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    notes           TEXT
)
"""

def _ddl_extraction_cache() -> str:
    return """
CREATE TABLE IF NOT EXISTS extraction_cache (
    description_hash TEXT PRIMARY KEY,
    result_json      TEXT NOT NULL,
    cached_at        TEXT NOT NULL
)
"""

def _ddl_skill_gap_snapshots() -> str:
    pk = "SERIAL PRIMARY KEY" if _POSTGRES_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return f"""
CREATE TABLE IF NOT EXISTS skill_gap_snapshots (
    id               {pk},
    run_id           TEXT NOT NULL,
    computed_at      TEXT NOT NULL,
    skill            TEXT NOT NULL,
    listings_blocked INTEGER NOT NULL,
    opportunity_cost REAL NOT NULL,
    mean_score       REAL NOT NULL,
    top_score        INTEGER NOT NULL,
    example_ids      TEXT NOT NULL,
    also_nice_to_have INTEGER NOT NULL DEFAULT 0,
    sample_size      INTEGER NOT NULL,
    low_confidence   INTEGER NOT NULL DEFAULT 0
)
"""

def _ddl_query_log() -> str:
    pk = "SERIAL PRIMARY KEY" if _POSTGRES_URL else "INTEGER PRIMARY KEY AUTOINCREMENT"
    return f"""
CREATE TABLE IF NOT EXISTS query_log (
    id          {pk},
    question    TEXT NOT NULL,
    tool_used   TEXT,
    params      TEXT,
    answerable  INTEGER NOT NULL,
    duration_s  REAL NOT NULL,
    timestamp   TEXT NOT NULL
)
"""

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

class _CursorWrapper:
    """Wraps either sqlite3 or psycopg2 cursor to provide a unified interface."""
    def __init__(self, cursor, is_postgres=False):
        self._cursor = cursor
        self.is_postgres = is_postgres

    def _translate_sql(self, sql: str) -> str:
        if not self.is_postgres:
            return sql
            
        # Translate placeholders
        sql = re.sub(r'\?(?![\w:])', '%s', sql)
        sql = re.sub(r':(\w+)', r'%(\1)s', sql)
        
        # Translate date arithmetic
        sql = sql.replace("datetime('now', '-' || ? || ' days')", "NOW() - CAST(%s || ' days' AS INTERVAL)")
        sql = sql.replace("datetime('now', '-' || %s || ' days')", "NOW() - CAST(%s || ' days' AS INTERVAL)")
        
        return sql

    def execute(self, sql: str, params: Any = None):
        sql = self._translate_sql(sql)
        if params is not None:
            self._cursor.execute(sql, params)
        else:
            self._cursor.execute(sql)
        return self

    def executemany(self, sql: str, params: list[Any]):
        sql = self._translate_sql(sql)
        self._cursor.executemany(sql, params)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if not row:
            return None
        return dict(row)

    def fetchall(self):
        return [dict(r) for r in self._cursor.fetchall()]


@contextmanager
def _connect(path: str) -> Generator[_CursorWrapper, None, None]:
    if _POSTGRES_URL:
        conn = psycopg2.connect(_POSTGRES_URL)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        wrapper = _CursorWrapper(cursor, is_postgres=True)
    else:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        wrapper = _CursorWrapper(cursor, is_postgres=False)

    try:
        yield wrapper
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        if not _POSTGRES_URL:
            cursor.close()
        else:
            cursor.close()
        conn.close()

# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def init_db(path: str) -> None:
    """Create all tables if they do not already exist."""
    with _connect(path) as conn:
        conn.execute(_ddl_listings())
        conn.execute(_ddl_skill_gaps())
        conn.execute(_ddl_cycle_log())
        conn.execute(_ddl_extraction_cache())
        conn.execute(_ddl_skill_gap_snapshots())
        conn.execute(_ddl_query_log())
        
        # We handle scored_at natively in DDL now, but for old SQLite:
        if not _POSTGRES_URL:
            _add_column_if_missing(conn, "listings", "scored_at", "TEXT")

def _add_column_if_missing(
    conn: _CursorWrapper,
    table: str,
    column: str,
    col_type: str,
) -> None:
    """ALTER TABLE … ADD COLUMN only when the column does not yet exist."""
    existing = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

def make_listing_id(source: str, url: str) -> str:
    raw = f"{source}::{url}".encode()
    return hashlib.sha256(raw).hexdigest()

def upsert_listings(path: str, rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0

    if _POSTGRES_URL:
        sql = """
            INSERT INTO listings
                (id, title, company, location, url, description,
                 source, posted_at, fetched_at, fit_score, fit_reason)
            VALUES
                (%(id)s, %(title)s, %(company)s, %(location)s, %(url)s, %(description)s,
                 %(source)s, %(posted_at)s, %(fetched_at)s, %(fit_score)s, %(fit_reason)s)
            ON CONFLICT (id) DO NOTHING
        """
    else:
        sql = """
            INSERT OR IGNORE INTO listings
                (id, title, company, location, url, description,
                 source, posted_at, fetched_at, fit_score, fit_reason)
            VALUES
                (:id, :title, :company, :location, :url, :description,
                 :source, :posted_at, :fetched_at, :fit_score, :fit_reason)
        """
        
    now = _utcnow()
    prepped: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        entry.setdefault("id", make_listing_id(entry["source"], entry["url"]))
        entry.setdefault("fetched_at", now)
        entry.setdefault("fit_score", None)
        entry.setdefault("fit_reason", None)
        prepped.append(entry)

    with _connect(path) as conn:
        before = _row_count(conn, "listings")
        conn.executemany(sql, prepped)
        after = _row_count(conn, "listings")

    return after - before


def count_unscored(path: str) -> int:
    sql = "SELECT COUNT(*) FROM listings WHERE fit_score IS NULL"
    with _connect(path) as conn:
        res = conn.execute(sql).fetchone()
        return list(res.values())[0] if res else 0


def last_fetch_time(path: str) -> str | None:
    sql = "SELECT MAX(fetched_at) FROM listings"
    with _connect(path) as conn:
        res = conn.execute(sql).fetchone()
        return list(res.values())[0] if res else None


def log_cycle(
    path: str,
    agent: str,
    started_at: str,
    finished_at: str,
    records_touched: int,
    status: str,
    notes: str | None = None,
) -> None:
    sql = """
        INSERT INTO cycle_log
            (agent, started_at, finished_at, records_touched, status, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    with _connect(path) as conn:
        conn.execute(sql, (agent, started_at, finished_at,
                           records_touched, status, notes))


def get_listings(
    path: str,
    limit: int = 100,
    min_score: int | None = None,
) -> list[dict[str, Any]]:
    if min_score is not None:
        sql = """
            SELECT * FROM listings
            WHERE fit_score >= ?
            ORDER BY fit_score DESC
            LIMIT ?
        """
        params: tuple = (min_score, limit)
    else:
        sql = "SELECT * FROM listings ORDER BY fit_score DESC LIMIT ?"
        params = (limit,)

    with _connect(path) as conn:
        rows = conn.execute(sql, params).fetchall()
    return rows


def get_companies_hiring(path: str, days: int) -> list[dict[str, Any]]:
    sql = """
        SELECT company, COUNT(*) as count
        FROM listings
        WHERE posted_at >= datetime('now', '-' || ? || ' days')
        GROUP BY company
        ORDER BY count DESC
    """
    with _connect(path) as conn:
        rows = conn.execute(sql, (days,)).fetchall()
    return rows


def write_score(
    path: str,
    listing_id: str,
    score: int,
    reason: str,
    components: dict[str, Any],
) -> None:
    sql = """
        UPDATE listings
        SET fit_score  = ?,
            fit_reason = ?,
            scored_at  = ?
        WHERE id = ?
    """
    with _connect(path) as conn:
        conn.execute(sql, (score, reason, _utcnow(), listing_id))


def get_unscored_listings(path: str, limit: int) -> list[dict[str, Any]]:
    sql = """
        SELECT * FROM listings
        WHERE fit_score IS NULL
        ORDER BY fetched_at ASC
        LIMIT ?
    """
    with _connect(path) as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return rows


def get_extraction_cache(path: str, description_hash: str) -> dict[str, Any] | None:
    sql = "SELECT result_json FROM extraction_cache WHERE description_hash = ?"
    with _connect(path) as conn:
        row = conn.execute(sql, (description_hash,)).fetchone()
    if row is None:
        return None
    return json.loads(row["result_json"])


def set_extraction_cache(
    path: str,
    description_hash: str,
    result: dict[str, Any],
) -> None:
    if _POSTGRES_URL:
        sql = """
            INSERT INTO extraction_cache
                (description_hash, result_json, cached_at)
            VALUES (?, ?, ?)
            ON CONFLICT (description_hash) DO UPDATE 
            SET result_json = EXCLUDED.result_json, cached_at = EXCLUDED.cached_at
        """
    else:
        sql = """
            INSERT OR REPLACE INTO extraction_cache
                (description_hash, result_json, cached_at)
            VALUES (?, ?, ?)
        """
    with _connect(path) as conn:
        conn.execute(sql, (description_hash, json.dumps(result), _utcnow()))


def get_scored_listings_with_cache(path: str) -> list[dict[str, Any]]:
    import hashlib as _hashlib

    scored_sql = "SELECT * FROM listings WHERE fit_score IS NOT NULL"
    with _connect(path) as conn:
        listing_rows = conn.execute(scored_sql).fetchall()

    listings = [dict(r) for r in listing_rows]
    if not listings:
        return listings

    id_to_hash: dict[str, str] = {}
    for row in listings:
        desc = row.get("description") or ""
        if desc:
            h = _hashlib.sha256(desc.encode("utf-8", errors="replace")).hexdigest()
            id_to_hash[row["id"]] = h

    all_hashes = list(set(id_to_hash.values()))
    if all_hashes:
        placeholders = ",".join("?" * len(all_hashes))
        cache_sql = (
            "SELECT description_hash, result_json "
            "FROM extraction_cache "
            f"WHERE description_hash IN ({placeholders})"
        )
        with _connect(path) as conn:
            cache_rows = conn.execute(cache_sql, all_hashes).fetchall()
        cache: dict[str, Any] = {
            r["description_hash"]: json.loads(r["result_json"])
            for r in cache_rows
        }
    else:
        cache = {}

    for listing in listings:
        h = id_to_hash.get(listing["id"])
        listing["facts"] = cache.get(h) if h else None

    return listings


def write_gap_snapshot(
    path: str,
    run_id: str,
    computed_at: str,
    gaps: list[dict[str, Any]],
) -> None:
    if not gaps:
        return
        
    sql = """
        INSERT INTO skill_gap_snapshots
            (run_id, computed_at, skill, listings_blocked, opportunity_cost,
             mean_score, top_score, example_ids, also_nice_to_have,
             sample_size, low_confidence)
        VALUES
            (%(run_id)s, %(computed_at)s, %(skill)s, %(listings_blocked)s, %(opportunity_cost)s,
             %(mean_score)s, %(top_score)s, %(example_ids)s, %(also_nice_to_have)s,
             %(sample_size)s, %(low_confidence)s)
    """ if _POSTGRES_URL else """
        INSERT INTO skill_gap_snapshots
            (run_id, computed_at, skill, listings_blocked, opportunity_cost,
             mean_score, top_score, example_ids, also_nice_to_have,
             sample_size, low_confidence)
        VALUES
            (:run_id, :computed_at, :skill, :listings_blocked, :opportunity_cost,
             :mean_score, :top_score, :example_ids, :also_nice_to_have,
             :sample_size, :low_confidence)
    """
    
    rows = []
    for g in gaps:
        rows.append({
            "run_id":           run_id,
            "computed_at":      computed_at,
            "skill":            g["skill"],
            "listings_blocked": g["listings_blocked"],
            "opportunity_cost": g["opportunity_cost"],
            "mean_score":       g["mean_score"],
            "top_score":        g["top_score"],
            "example_ids":      json.dumps(g["example_ids"]),
            "also_nice_to_have": g["also_nice_to_have"],
            "sample_size":      g["sample_size"],
            "low_confidence":   int(g["low_confidence"]),
        })
    with _connect(path) as conn:
        conn.executemany(sql, rows)


def get_latest_snapshot(path: str) -> list[dict[str, Any]]:
    run_sql = """
        SELECT run_id FROM skill_gap_snapshots
        ORDER BY computed_at DESC
        LIMIT 1
    """
    with _connect(path) as conn:
        row = conn.execute(run_sql).fetchone()
        if row is None:
            return []
        run_id = row["run_id"]
        rows = conn.execute(
            """
            SELECT * FROM skill_gap_snapshots
            WHERE run_id = ?
            ORDER BY opportunity_cost DESC
            """,
            (run_id,),
        ).fetchall()
        
    result = []
    for r in rows:
        d = dict(r)
        d["example_ids"] = json.loads(d["example_ids"])
        result.append(d)
    return result


def get_snapshot_run_ids(path: str) -> list[tuple[str, str]]:
    sql = """
        SELECT run_id, MIN(computed_at) AS computed_at
        FROM   skill_gap_snapshots
        GROUP  BY run_id
        ORDER  BY computed_at ASC
    """
    with _connect(path) as conn:
        rows = conn.execute(sql).fetchall()
    return [(r["run_id"], r["computed_at"]) for r in rows]


def get_snapshot_by_run_id(path: str, run_id: str) -> list[dict[str, Any]]:
    sql = """
        SELECT * FROM skill_gap_snapshots
        WHERE  run_id = ?
        ORDER  BY opportunity_cost DESC
    """
    with _connect(path) as conn:
        rows = conn.execute(sql, (run_id,)).fetchall()
        
    result = []
    for r in rows:
        d = dict(r)
        d["example_ids"] = json.loads(d["example_ids"])
        result.append(d)
    return result


def last_scored_at(path: str) -> str | None:
    sql = "SELECT MAX(scored_at) FROM listings"
    with _connect(path) as conn:
        res = conn.execute(sql).fetchone()
        return list(res.values())[0] if res else None


def last_gap_snapshot_at(path: str) -> str | None:
    sql = "SELECT MAX(computed_at) FROM skill_gap_snapshots"
    with _connect(path) as conn:
        res = conn.execute(sql).fetchone()
        return list(res.values())[0] if res else None


def last_cycle_verdict(path: str) -> tuple[str | None, str | None]:
    sql = """
        SELECT status, started_at FROM cycle_log
        WHERE  agent IN ('cycle', 'Orchestrator')
        ORDER  BY started_at DESC
        LIMIT  1
    """
    with _connect(path) as conn:
        row = conn.execute(sql).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT status, started_at FROM cycle_log "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            
    if row is None:
        return (None, None)
    return (row["status"], row["started_at"])


def get_latest_passing_cycle(path: str) -> dict[str, Any] | None:
    sql = """
        SELECT * FROM cycle_log
        WHERE  agent IN ('cycle', 'Orchestrator')
        AND    status != 'degraded'
        ORDER  BY started_at DESC
        LIMIT  1
    """
    with _connect(path) as conn:
        row = conn.execute(sql).fetchone()
    if row is None:
        return None
    return dict(row)


def get_recent_cycles(path: str, limit: int = 30) -> list[dict[str, Any]]:
    sql = """
        SELECT * FROM cycle_log
        WHERE agent IN ('cycle', 'Orchestrator')
        ORDER  BY started_at DESC
        LIMIT  ?
    """
    with _connect(path) as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return rows


def get_listing_counts(path: str) -> dict[str, Any]:
    sql = """
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN fit_score IS NOT NULL THEN 1 ELSE 0 END) as scored,
            SUM(CASE WHEN fit_score IS NULL THEN 1 ELSE 0 END) as unscored,
            MAX(posted_at) as newest_listing
        FROM listings
    """
    with _connect(path) as conn:
        row = conn.execute(sql).fetchone()
    return dict(row) if row else {}


def get_gap_detail(path: str, skill: str) -> list[dict[str, Any]]:
    sql_run = "SELECT run_id FROM skill_gap_snapshots ORDER BY computed_at DESC LIMIT 1"
    with _connect(path) as conn:
        row = conn.execute(sql_run).fetchone()
        if not row:
            return []
        run_id = row["run_id"]
        
        sql_snap = "SELECT example_ids FROM skill_gap_snapshots WHERE run_id = ? AND skill = ?"
        snap = conn.execute(sql_snap, (run_id, skill)).fetchone()
        if not snap or not snap["example_ids"]:
            return []
            
        ids = [i.strip() for i in snap["example_ids"].split(",") if i.strip()]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        
        sql_list = f"""
            SELECT id, title, company, fit_score, url 
            FROM listings 
            WHERE id IN ({placeholders})
            ORDER BY fit_score DESC
        """
        rows = conn.execute(sql_list, ids).fetchall()
    return rows


def get_trend(path: str, weeks: int) -> list[dict[str, Any]]:
    sql = """
        SELECT skill, computed_at, opportunity_cost
        FROM skill_gap_snapshots
        WHERE computed_at >= datetime('now', '-' || ? || ' days')
        ORDER BY computed_at ASC
    """
    with _connect(path) as conn:
        rows = conn.execute(sql, (weeks * 7,)).fetchall()
    return rows


def get_skill_demand(path: str, skill: str) -> dict[str, int]:
    sql = "SELECT result_json FROM extraction_cache"
    required = 0
    nice_to_have = 0
    skill_lower = skill.lower()
    with _connect(path) as conn:
        rows = conn.execute(sql).fetchall()
        for row in rows:
            try:
                data = json.loads(row["result_json"])
                reqs = [s.lower() for s in data.get("required_skills", [])]
                nices = [s.lower() for s in data.get("nice_to_have_skills", [])]
                if skill_lower in reqs:
                    required += 1
                if skill_lower in nices:
                    nice_to_have += 1
            except Exception:
                pass
    return {"required": required, "nice_to_have": nice_to_have}


def log_query(path: str, question: str, tool_used: str | None, params: dict, answerable: bool, duration_s: float) -> None:
    sql = """
        INSERT INTO query_log (question, tool_used, params, answerable, duration_s, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """
    p_str = json.dumps(params) if params else None
    with _connect(path) as conn:
        conn.execute(sql, (question, tool_used, p_str, int(answerable), duration_s, _utcnow()))


def _row_count(conn: _CursorWrapper, table: str) -> int:
    res = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return list(res.values())[0]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# CLI Handlers
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    
    from edgedash.config import load_config
    try:
        cfg = load_config()
        DB = cfg.db_path
    except Exception:
        DB = "edgedash.db"
        
    if "--migrate" in sys.argv:
        backend = 'postgres' if _POSTGRES_URL else 'sqlite'
        print(f"[storage --migrate] backend={backend}")
        init_db(DB)
        print("[storage --migrate] done - all tables exist.")
        sys.exit(0)
        
    if "--check" in sys.argv:
        print(f"Backend active : {'Postgres' if _POSTGRES_URL else 'SQLite'}")
        if _POSTGRES_URL:
            print(f"Connection URL : {_POSTGRES_URL}")
        else:
            print(f"Database File  : {DB}")
            
        try:
            with _connect(DB) as conn:
                for table in ["listings", "skill_gaps", "cycle_log", "extraction_cache", "skill_gap_snapshots", "query_log"]:
                    try:
                        count = _row_count(conn, table)
                        print(f"Table {table:<22}: {count} rows")
                    except Exception as e:
                        print(f"Table {table:<22}: ERROR - {e}")
        except Exception as e:
            print(f"Connection failed: {e}")
        sys.exit(0)
