"""
storage.py — the ONLY module permitted to import sqlite3.

Exposes a thin interface over the database so that swapping the backend
(e.g. to Postgres in week 4) requires changes to this file only.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Any

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_DDL_LISTINGS = """
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
    fit_reason  TEXT
)
"""

_DDL_SKILL_GAPS = """
CREATE TABLE IF NOT EXISTS skill_gaps (
    skill       TEXT PRIMARY KEY,
    frequency   INTEGER NOT NULL DEFAULT 1,
    last_seen   TEXT NOT NULL
)
"""

_DDL_CYCLE_LOG = """
CREATE TABLE IF NOT EXISTS cycle_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent           TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    records_touched INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL,
    notes           TEXT
)
"""

_DDL_EXTRACTION_CACHE = """
CREATE TABLE IF NOT EXISTS extraction_cache (
    description_hash TEXT PRIMARY KEY,
    result_json      TEXT NOT NULL,
    cached_at        TEXT NOT NULL
)
"""

_DDL_SKILL_GAP_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS skill_gap_snapshots (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
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

# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextmanager
def _connect(path: str) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def init_db(path: str) -> None:
    """Create all tables if they do not already exist."""
    with _connect(path) as conn:
        conn.execute(_DDL_LISTINGS)
        conn.execute(_DDL_SKILL_GAPS)
        conn.execute(_DDL_CYCLE_LOG)
        conn.execute(_DDL_EXTRACTION_CACHE)
        conn.execute(_DDL_SKILL_GAP_SNAPSHOTS)
        # Safe migrations for columns added after initial release.
        _add_column_if_missing(conn, "listings", "scored_at", "TEXT")


def make_listing_id(source: str, url: str) -> str:
    """Return a stable, collision-resistant hash used as a listing primary key."""
    raw = f"{source}::{url}".encode()
    return hashlib.sha256(raw).hexdigest()


def upsert_listings(path: str, rows: list[dict[str, Any]]) -> int:
    """Insert new listings; skip rows whose id already exists.

    Returns the count of genuinely new rows inserted.
    """
    if not rows:
        return 0

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
    """Return the number of listings that have not yet been scored."""
    sql = "SELECT COUNT(*) FROM listings WHERE fit_score IS NULL"
    with _connect(path) as conn:
        return conn.execute(sql).fetchone()[0]


def last_fetch_time(path: str) -> str | None:
    """Return the most recent fetched_at timestamp, or None if table is empty."""
    sql = "SELECT MAX(fetched_at) FROM listings"
    with _connect(path) as conn:
        result = conn.execute(sql).fetchone()[0]
    return result


def log_cycle(
    path: str,
    agent: str,
    started_at: str,
    finished_at: str,
    records_touched: int,
    status: str,
    notes: str | None = None,
) -> None:
    """Write one row to cycle_log recording the outcome of an agent run."""
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
    """Return listings ordered by fit_score descending.

    Filters to fit_score >= min_score when provided.
    """
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

    return [dict(row) for row in rows]


def write_score(
    path: str,
    listing_id: str,
    score: int,
    reason: str,
    components: dict[str, Any],
) -> None:
    """Write fit_score, fit_reason, components, and scored_at for one listing."""
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
    """Return up to *limit* listings where fit_score IS NULL (rule 18)."""
    sql = """
        SELECT * FROM listings
        WHERE fit_score IS NULL
        ORDER BY fetched_at ASC
        LIMIT ?
    """
    with _connect(path) as conn:
        rows = conn.execute(sql, (limit,)).fetchall()
    return [dict(row) for row in rows]


def get_extraction_cache(path: str, description_hash: str) -> dict[str, Any] | None:
    """Return the cached extraction result for *description_hash*, or None on miss."""
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
    """Store *result* in the extraction cache keyed on *description_hash*."""
    sql = """
        INSERT OR REPLACE INTO extraction_cache
            (description_hash, result_json, cached_at)
        VALUES (?, ?, ?)
    """
    with _connect(path) as conn:
        conn.execute(sql, (description_hash, json.dumps(result), _utcnow()))


def get_scored_listings_with_cache(path: str) -> list[dict[str, Any]]:
    """Return all scored listings joined with their extraction cache entry.

    Each dict is a listing with an extra "facts" key holding the parsed
    extraction result, or None if no cache entry exists for that description.
    Only listings with a non-null fit_score are included.
    """
    import hashlib as _hashlib

    scored_sql = "SELECT * FROM listings WHERE fit_score IS NOT NULL"
    with _connect(path) as conn:
        listing_rows = conn.execute(scored_sql).fetchall()

    listings = [dict(r) for r in listing_rows]
    if not listings:
        return listings

    # Build description_hash -> listing_id mapping for cache lookup.
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
    """Append a full snapshot of gap rows for this run.

    Never overwrites previous runs — each run_id produces new rows (rule 25).
    """
    if not gaps:
        return
    sql = """
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
    """Return all rows from the most recent gap snapshot run, ranked by opportunity_cost."""
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
    """Return (run_id, computed_at) for every distinct run, oldest first."""
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
    """Return all rows for *run_id*, ranked by opportunity_cost descending."""
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
    """Return the most recent scored_at timestamp across all listings, or None."""
    sql = "SELECT MAX(scored_at) FROM listings"
    with _connect(path) as conn:
        return conn.execute(sql).fetchone()[0]


def last_gap_snapshot_at(path: str) -> str | None:
    """Return the computed_at of the most recent gap snapshot run, or None."""
    sql = "SELECT MAX(computed_at) FROM skill_gap_snapshots"
    with _connect(path) as conn:
        return conn.execute(sql).fetchone()[0]


def last_cycle_verdict(path: str) -> tuple[str | None, str | None]:
    """Return (status, started_at) of the most recent top-level cycle log row.

    A top-level row is one whose agent name is 'cycle' or 'Orchestrator'.
    Falls back to the most recent row of any agent if none found.
    """
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _row_count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table: str,
    column: str,
    col_type: str,
) -> None:
    """ALTER TABLE … ADD COLUMN only when the column does not yet exist."""
    existing = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
