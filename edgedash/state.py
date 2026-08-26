"""
state.py — cheap system state inspection (steering rule 28).

No LLM. No network. Arithmetic on timestamps and counts only.

Public API
----------
    read_state(config, now) -> SystemState

`now` is a required parameter — never called inside. This makes the
function fully testable without time mocking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import edgedash.storage as storage
from edgedash.config import Config


@dataclass(frozen=True)
class SystemState:
    # Fetch recency
    last_fetch_at: str | None           # ISO timestamp of most recent fetch
    hours_since_fetch: float | None     # None means never fetched

    # Scoring backlog
    unscored_count: int                 # listings with fit_score IS NULL

    # Gap analysis freshness
    gaps_computed_at: str | None        # timestamp of most recent gap snapshot
    gaps_stale: bool                    # True if any score is newer than snapshot

    # Last cycle outcome
    last_cycle_verdict: str | None      # "ok" / "failed" / "partial" or None
    last_cycle_at: str | None           # timestamp of last cycle log row


def read_state(config: Config, now: datetime) -> SystemState:
    """Read lightweight system state from the database.

    All queries are aggregates (MAX, COUNT) — no full table scans.

    Parameters
    ----------
    config: loaded Config (supplies db_path)
    now:    caller-supplied current time; never datetime.now() inside here
    """
    db = config.db_path

    last_fetch_at = storage.last_fetch_time(db)
    hours_since_fetch = _hours_between(last_fetch_at, now)

    unscored_count = storage.count_unscored(db)

    gaps_computed_at = storage.last_gap_snapshot_at(db)
    last_scored = storage.last_scored_at(db)
    gaps_stale = _is_stale(gaps_computed_at, last_scored)

    verdict, last_cycle_at = storage.last_cycle_verdict(db)

    return SystemState(
        last_fetch_at=last_fetch_at,
        hours_since_fetch=hours_since_fetch,
        unscored_count=unscored_count,
        gaps_computed_at=gaps_computed_at,
        gaps_stale=gaps_stale,
        last_cycle_verdict=verdict,
        last_cycle_at=last_cycle_at,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 timestamp, normalising to UTC."""
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _hours_between(ts: str | None, now: datetime) -> float | None:
    """Return the number of hours between *ts* and *now*, or None if ts is None."""
    if ts is None:
        return None
    try:
        then = _parse_iso(ts)
        delta = now - then
        return max(0.0, delta.total_seconds() / 3600)
    except (ValueError, OverflowError):
        return None


def _is_stale(gaps_computed_at: str | None, last_scored: str | None) -> bool:
    """Return True if there are scores newer than the latest gap snapshot.

    Also returns True when gaps have never been computed (None).
    Returns False when there are no scores at all yet.
    """
    if gaps_computed_at is None:
        # No snapshot ever written — need to run.
        return True
    if last_scored is None:
        # No scores exist yet — nothing to go stale.
        return False
    try:
        return _parse_iso(last_scored) > _parse_iso(gaps_computed_at)
    except (ValueError, OverflowError):
        return True
