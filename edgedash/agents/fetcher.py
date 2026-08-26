"""
fetcher.py — real Fetcher agent.

Reads the enabled source names from config.sources, instantiates each
registered Source in turn, and writes normalised rows via
storage.upsert_listings.

Per steering rule 12: a single failing source never kills the cycle.
Each source failure is caught, logged to cycle_log, and skipped — the
remaining sources continue.  The agent reports "ok" as long as at least
one source succeeded; "failed" only if every attempted source failed.
"""

from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime, timezone

import edgedash.sources as _sources_pkg
import edgedash.storage as storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config
from edgedash.sources.base import SOURCES
from edgedash.sources.http import SourceError


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ensure_sources_loaded() -> None:
    """Import every module in edgedash/sources/ so @register decorators fire."""
    for module_info in pkgutil.iter_modules(_sources_pkg.__path__):
        if module_info.name.startswith("_"):
            continue
        importlib.import_module(f"edgedash.sources.{module_info.name}")


def _normalised_to_storage(row: dict) -> dict:
    """Convert a Source-normalised dict to a storage.upsert_listings row."""
    source = row.get("source") or "unknown"
    url = row.get("url") or ""
    external_id = row.get("external_id")
    # Reuse storage.make_listing_id — never duplicate the hash logic here.
    listing_id = storage.make_listing_id(source, external_id if external_id else url)
    return {
        "id":          listing_id,
        "title":       row.get("title"),
        "company":     row.get("company"),
        "location":    row.get("location"),
        "url":         url or None,
        "description": row.get("description"),
        "source":      source,
        "posted_at":   row.get("posted_at"),
        "fetched_at":  datetime.now(timezone.utc).isoformat(),
        "fit_score":   None,
        "fit_reason":  None,
    }


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class Fetcher:
    name: str = "Fetcher"

    def run(
        self,
        config: Config,
        storage_path: str,
        stop_conditions: "StopConditions | None" = None,
    ) -> AgentResult:
        from edgedash.planning import StopConditions as SC
        _ensure_sources_loaded()

        enabled = config.sources
        if not enabled:
            return AgentResult(
                agent=self.name,
                status="failed",
                records_touched=0,
                notes="No sources listed in config.sources.",
            )

        # Respect stop_conditions.max_items if given, else fall back to config.
        max_listings = (
            stop_conditions.max_items
            if stop_conditions and stop_conditions.max_items is not None
            else config.fetch_max_listings
        )
        max_pages = (
            stop_conditions.max_pages
            if stop_conditions and stop_conditions.max_pages is not None
            else config.fetch_max_pages
        )

        total_new = 0
        note_parts: list[str] = []
        any_success = False

        # Build a config copy with stop_condition limits applied,
        # so sources read them from config without any interface change.
        from dataclasses import replace as _replace
        effective_config = _replace(
            config,
            fetch_max_pages=max_pages,
            fetch_max_listings=max_listings,
        ) if (max_pages != config.fetch_max_pages
              or max_listings != config.fetch_max_listings) else config

        for source_name in enabled:
            source_cls = SOURCES.get(source_name)
            if source_cls is None:
                msg = f"{source_name}: UNKNOWN (not registered)"
                print(f"  [Fetcher] {msg}")
                note_parts.append(msg)
                continue

            started_at = datetime.now(timezone.utc).isoformat()
            try:
                rows = source_cls().fetch(effective_config)
                # Respect max_listings cap across all sources combined.
                remaining = max_listings - total_new
                if remaining <= 0:
                    note_parts.append(f"{source_name}: skipped (max_listings reached)")
                    break
                rows = rows[:remaining]
                storage_rows = [_normalised_to_storage(r) for r in rows]
                new_count = storage.upsert_listings(storage_path, storage_rows)
                total_new += new_count
                any_success = True
                finished_at = datetime.now(timezone.utc).isoformat()
                storage.log_cycle(
                    path=storage_path,
                    agent=f"Fetcher/{source_name}",
                    started_at=started_at,
                    finished_at=finished_at,
                    records_touched=new_count,
                    status="ok",
                    notes=f"{len(rows)} rows fetched, {new_count} new",
                )
                note_parts.append(f"{source_name}: {len(rows)} rows ({new_count} new)")

            except (SourceError, Exception) as exc:  # noqa: BLE001
                finished_at = datetime.now(timezone.utc).isoformat()
                reason = f"{type(exc).__name__}: {exc}"
                short = str(exc) or type(exc).__name__
                print(f"  [Fetcher] {source_name}: FAILED — {short}")
                storage.log_cycle(
                    path=storage_path,
                    agent=f"Fetcher/{source_name}",
                    started_at=started_at,
                    finished_at=finished_at,
                    records_touched=0,
                    status="failed",
                    notes=reason,
                )
                note_parts.append(f"{source_name}: FAILED ({short})")

        status = "ok" if any_success else "failed"
        notes = " | ".join(note_parts)

        return AgentResult(
            agent=self.name,
            status=status,
            records_touched=total_new,
            notes=notes,
        )
