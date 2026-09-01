"""
verifier.py — Verifier agent (rule 34: writes NO data, only reads).

Runs all deterministic verification checks from verification.py against
the current cycle's data and returns a Verdict via AgentResult.

No LLM. No writes. Pure read-only verification.
"""

from __future__ import annotations

from datetime import datetime, timezone

import edgedash.storage as storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config
from edgedash.planning import StopConditions
from edgedash.verification import run_all_checks


class Verifier:
    name: str = "Verifier"

    def run(
        self,
        config: Config,
        storage_path: str,
        stop_conditions: StopConditions | None = None,
    ) -> AgentResult:
        now = datetime.now(timezone.utc)

        # ── Gather data (read-only) ──────────────────────────────────────
        # 1. Scores from all scored listings
        scored = storage.get_listings(storage_path, limit=10_000, min_score=0)
        scores = [row["fit_score"] for row in scored if row.get("fit_score") is not None]

        # 2. Extracted facts from cache
        listings_with_facts = storage.get_scored_listings_with_cache(storage_path)
        facts_list = [
            row["facts"]
            for row in listings_with_facts
            if row.get("facts") is not None
        ]

        # 3. Latest gap snapshot
        gaps = storage.get_latest_snapshot(storage_path)

        # 4. Latest fetch time
        latest_fetch_at = storage.last_fetch_time(storage_path)

        # ── Run checks ───────────────────────────────────────────────────
        verdict = run_all_checks(
            scores=scores,
            facts_list=facts_list,
            gaps=gaps,
            latest_fetch_at=latest_fetch_at,
            config=config,
            now=now,
        )

        # ── Build notes ──────────────────────────────────────────────────
        if verdict.passed:
            notes = f"VERDICT: pass — {verdict.summary}"
        else:
            details = " | ".join(
                f"{c.name} observed {c.observed} (min {c.threshold})"
                for c in verdict.failed_checks
            )
            notes = f"VERDICT: fail — {details}"

        return AgentResult(
            agent=self.name,
            status="ok" if verdict.passed else "failed",
            records_touched=0,
            notes=notes,
        )
