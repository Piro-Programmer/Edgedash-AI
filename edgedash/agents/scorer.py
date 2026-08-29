"""
scorer.py — Scorer agent.

Selects unscored listings, extracts facts via extractor.extract(), scores
them via scoring.score_listing(), writes results via storage.write_score(),
and logs the score distribution per rule 20.

Per rule 17: one listing failure is one skipped listing. The loop continues.
Per rule 18: only listings WHERE fit_score IS NULL are touched.
Per rule 21: batch capped at config.llm_batch_size.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import edgedash.storage as storage
from edgedash.agents.base import AgentResult
from edgedash.agents.extractor import extract
from edgedash.config import Config
from edgedash.llm import LLMError
from edgedash.scoring import score_listing

_SUSPECT_SPREAD_THRESHOLD = 10   # spread < this → flag as suspect (rule 20)


class Scorer:
    name: str = "Scorer"

    def run(
        self,
        config: Config,
        storage_path: str,
        stop_conditions: "StopConditions | None" = None,
    ) -> AgentResult:
        from edgedash.planning import StopConditions as SC

        # Resolve limits: stop_conditions take priority over config defaults.
        batch_size = (
            stop_conditions.max_items
            if stop_conditions and stop_conditions.max_items is not None
            else config.llm_batch_size
        )
        max_seconds = (
            stop_conditions.max_seconds
            if stop_conditions and stop_conditions.max_seconds is not None
            else config.score_max_seconds
        )
        deadline = time.monotonic() + max_seconds if max_seconds else None

        # strict_scoring: verification retry flag — widen distribution.
        strict = (
            stop_conditions.strict_scoring
            if stop_conditions is not None
            else False
        )

        batch = storage.get_unscored_listings(storage_path, batch_size)

        if not batch:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="no unscored listings",
            )

        scored_count = 0
        failed_count = 0
        scores: list[int] = []
        timeout_note: str = ""

        for i, listing in enumerate(batch, start=1):
            # Respect max_seconds stop condition.
            if deadline and time.monotonic() > deadline:
                timeout_note = f" · stopped: max_seconds={max_seconds} reached"
                break
            listing_id: str = listing["id"]
            title: str = listing.get("title") or listing_id[:16]
            print(f"  [Scorer] {i}/{len(batch)} — {title[:60]}", flush=True)
            try:
                facts = extract(listing, storage_path, strict=strict)
                result = score_listing(listing, facts, config)

                storage.write_score(
                    path=storage_path,
                    listing_id=listing_id,
                    score=result["score"],
                    reason=result["reason"],
                    components=result["components"],
                )

                scores.append(result["score"])
                scored_count += 1

            except (LLMError, Exception) as exc:  # noqa: BLE001
                failed_count += 1
                storage.log_cycle(
                    path=storage_path,
                    agent=f"{self.name}/{listing_id[:16]}",
                    started_at=_utcnow(),
                    finished_at=_utcnow(),
                    records_touched=0,
                    status="failed",
                    notes=f"{title}: {type(exc).__name__}: {exc}",
                )
                msg = str(exc).lower()
                if "quota" in msg or "429" in msg or "resource_exhausted" in msg:
                    timeout_note = " · stopped: rate limit / quota exceeded"
                    break

        notes = _build_notes(scores, failed_count) + timeout_note
        dist_status = _distribution_status(scores)

        storage.log_cycle(
            path=storage_path,
            agent=self.name,
            started_at=_utcnow(),
            finished_at=_utcnow(),
            records_touched=scored_count,
            status=dist_status,
            notes=notes,
        )

        return AgentResult(
            agent=self.name,
            status="ok" if scored_count > 0 or failed_count == 0 else "failed",
            records_touched=scored_count,
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_notes(scores: list[int], failed: int) -> str:
    """Build the AgentResult notes string from the batch distribution."""
    if not scores:
        fail_str = f"{failed} failed" if failed else "none scored"
        return fail_str

    lo   = min(scores)
    hi   = max(scores)
    mean = round(sum(scores) / len(scores))
    spread = hi - lo

    spread_label = "spread OK" if spread >= _SUSPECT_SPREAD_THRESHOLD else "spread SUSPECT"
    fail_str = f" · {failed} failed" if failed else ""
    return (
        f"scored {len(scores)}"
        f" · range {lo}-{hi}"
        f" · mean {mean}"
        f"{fail_str}"
        f" · {spread_label}"
    )


def _distribution_status(scores: list[int]) -> str:
    """Return 'suspect' if all scores are within 10 points, else 'ok' (rule 20)."""
    if len(scores) < 2:
        return "ok"
    return "suspect" if (max(scores) - min(scores)) < _SUSPECT_SPREAD_THRESHOLD else "ok"

if __name__ == "__main__":
    import argparse
    from edgedash.config import load_config
    import edgedash.storage as storage

    parser = argparse.ArgumentParser(description="Run the Scorer agent standalone.")
    parser.add_argument("--limit", type=int, help="Override score_batch_size")
    args = parser.parse_args()

    cfg = load_config()
    if args.limit is not None:
        cfg.score_batch_size = args.limit

    # Ensure DB is initialized
    storage.init_db(cfg.db_path)

    scorer = Scorer()
    print(f"Running {scorer.name}...")
    result = scorer.run(cfg, cfg.db_path)

    print("\n--- Result ---")
    print(f"Status : {result.status}")
    print(f"Touched: {result.records_touched}")
    print(f"Notes  : {result.notes}")
