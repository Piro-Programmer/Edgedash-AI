"""
arbeitnow.py — ArbeitnowSource: free public job board API, no key required.

API docs: https://www.arbeitnow.com/api/job-board-api

Pagination strategy:
  Fetches pages 1–MAX_PAGES, stopping early when a page returns no
  keyword matches (signal that results are drifting off-topic).

Filtering strategy:
  1. Keyword filter  — title or description contains any config keyword.
  2. Location filter — location contains config.target_city.
  3. If the location filter would leave fewer than MIN_RESULTS, it is
     relaxed and the decision is printed so the caller can see it.
     Remote listings always pass the location filter.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from edgedash.config import Config
from edgedash.sources.base import register
from edgedash.sources.http import SourceError, get_json

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_API_URL    = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES  = 5
_MIN_RESULTS = 5
_REQ_DELAY  = 1.0   # seconds between requests (§ 14)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _epoch_to_iso(ts: int | None) -> str | None:
    """Convert a Unix timestamp to an ISO 8601 string, or return None."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _normalise(raw: dict[str, Any]) -> dict[str, Any]:
    """Map one Arbeitnow job dict to EdgeDash's canonical shape."""
    return {
        "source":      "arbeitnow",
        "external_id": raw.get("slug") or None,
        "title":       raw.get("title") or None,
        "company":     raw.get("company_name") or None,
        "location":    raw.get("location") or None,
        "url":         raw.get("url") or None,
        "description": raw.get("description") or None,
        "posted_at":   _epoch_to_iso(raw.get("created_at")),
        "raw":         raw,
    }


def _matches_keywords(row: dict[str, Any], keywords: list[str]) -> bool:
    """Return True if any keyword appears in title or description (case-insensitive)."""
    if not keywords:
        return True
    haystack = " ".join(
        filter(None, [row.get("title"), row.get("description")])
    ).lower()
    return any(kw.lower() in haystack for kw in keywords)


def _matches_city(row: dict[str, Any], city: str) -> bool:
    """Return True if the listing location contains the target city."""
    loc = (row.get("location") or "").lower()
    if "remote" in loc:
        return True
    return city.lower() in loc


# ---------------------------------------------------------------------------
# Source class
# ---------------------------------------------------------------------------

@register
class ArbeitnowSource:
    name: str = "arbeitnow"

    def fetch(self, config: Config) -> list[dict[str, Any]]:
        """Fetch, filter, and return normalised job listings.

        Prints a progress summary so the Fetcher agent can surface it
        in the cycle output.
        """
        raw: list[dict[str, Any]] = []

        for page in range(1, config.fetch_max_pages + 1):
            if page > 1:
                time.sleep(_REQ_DELAY)

            try:
                payload = get_json(_API_URL, params={"page": page})
            except SourceError as exc:
                print(f"  [arbeitnow] Page {page} failed: {exc}")
                break

            jobs: list[dict[str, Any]] = payload.get("data", [])
            if not jobs:
                break

            raw.extend(jobs)

            # Stop early if this page has no keyword matches at all
            if page > 1 and not any(
                _matches_keywords(j, config.keywords) for j in jobs
            ):
                print(f"  [arbeitnow] Page {page}: no keyword matches — stopping.")
                break

        print(f"  [arbeitnow] Fetched {len(raw)} raw listings across up to {config.fetch_max_pages} page(s).")

        # Step 1 — keyword filter
        kw_matched = [j for j in raw if _matches_keywords(j, config.keywords)]
        print(f"  [arbeitnow] After keyword filter : {len(kw_matched)} listings.")

        # Step 2 — location filter (with graceful relaxation)
        loc_matched = [j for j in kw_matched if _matches_city(j, config.target_city)]

        if len(loc_matched) < _MIN_RESULTS:
            print(
                f"  [arbeitnow] Location filter '{config.target_city}' would leave "
                f"only {len(loc_matched)} result(s) (min {_MIN_RESULTS}). "
                f"Relaxing — returning all keyword-matched listings."
            )
            final = kw_matched
        else:
            final = loc_matched

        print(f"  [arbeitnow] After location filter: {len(final)} listings.")

        return [_normalise(j) for j in final]
