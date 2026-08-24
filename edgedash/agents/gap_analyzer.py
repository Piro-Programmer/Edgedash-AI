"""
gap_analyzer.py — deterministic skill-gap analysis (steering rules 22-27).

No LLM calls. No network. Pure arithmetic over scored listings.

Algorithm
---------
For every scored listing that has extraction facts:
  for every required_skill that is NOT in config.my_skills (canonically):
    accumulate that skill's per-listing contribution

Per-skill metrics:
  listings_blocked  — count of listings requiring the skill
  opportunity_cost  — sum(score / 100) for those listings  [ranking key, rule 24]
  mean_score        — mean fit_score of blocked listings
  top_score         — highest fit_score among blocked listings
  example_ids       — up to 5 listing IDs, highest score first  [rule 26]
  also_nice_to_have — count where skill appears as nice_to_have (tracked separately)
  sample_size       — same as listings_blocked (explicit per rule 27)
  low_confidence    — True when sample_size < 3  (flagged per rule 27)

Rank by opportunity_cost descending. Report top 10.
Snapshot written via storage — never overwrites previous runs  (rule 25).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import edgedash.storage as storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config
from edgedash.skills import canonical

_LOW_CONFIDENCE_THRESHOLD = 3   # fewer listings than this → low confidence
_TOP_N = 10                     # gaps reported in the snapshot


class GapAnalyzer:
    name: str = "GapAnalyzer"

    def run(self, config: Config, storage_path: str) -> AgentResult:
        listings = storage.get_scored_listings_with_cache(storage_path)

        analysed = [l for l in listings if l.get("facts")]
        if not analysed:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="no scored listings with extracted facts yet",
            )

        my_skills = {canonical(s, config.skill_aliases) for s in config.my_skills}
        gaps = _compute_gaps(analysed, my_skills, config.skill_aliases)
        ranked = sorted(gaps.values(), key=lambda g: g["opportunity_cost"], reverse=True)
        top = ranked[:_TOP_N]

        run_id = uuid.uuid4().hex
        computed_at = datetime.now(timezone.utc).isoformat()
        storage.write_gap_snapshot(storage_path, run_id, computed_at, top)

        notes = _build_notes(top, len(analysed))
        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=len(top),
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Core arithmetic
# ---------------------------------------------------------------------------

def _compute_gaps(
    listings: list[dict[str, Any]],
    my_skills: set[str],
    aliases: dict[str, str],
) -> dict[str, dict[str, Any]]:
    """Return a dict keyed on canonical skill name containing gap metrics."""

    # accumulator: skill -> list of (listing_id, score)
    blocked: dict[str, list[tuple[str, int]]] = defaultdict(list)
    nice_counts: dict[str, int] = defaultdict(int)

    for listing in listings:
        facts: dict = listing.get("facts") or {}
        score: int = listing.get("fit_score") or 0
        lid: str = listing["id"]

        required: list[str] = facts.get("required_skills") or []
        nice: list[str] = facts.get("nice_to_have") or []

        for raw in required:
            skill = canonical(raw, aliases)
            if not skill:
                continue
            if skill not in my_skills:
                blocked[skill].append((lid, score))

        for raw in nice:
            skill = canonical(raw, aliases)
            if skill and skill not in my_skills:
                nice_counts[skill] += 1

    result: dict[str, dict[str, Any]] = {}
    for skill, entries in blocked.items():
        # Sort by score descending for example_ids and top_score.
        entries_sorted = sorted(entries, key=lambda x: x[1], reverse=True)
        scores = [s for _, s in entries_sorted]

        opportunity_cost = sum(s / 100.0 for s in scores)
        mean_score = round(sum(scores) / len(scores), 1)
        top_score = scores[0]
        example_ids = [lid for lid, _ in entries_sorted[:5]]
        sample_size = len(entries)

        result[skill] = {
            "skill":            skill,
            "listings_blocked": sample_size,
            "opportunity_cost": round(opportunity_cost, 2),
            "mean_score":       mean_score,
            "top_score":        top_score,
            "example_ids":      example_ids,
            "also_nice_to_have": nice_counts.get(skill, 0),
            "sample_size":      sample_size,
            "low_confidence":   sample_size < _LOW_CONFIDENCE_THRESHOLD,
        }

    return result


def _build_notes(top: list[dict[str, Any]], analysed: int) -> str:
    if not top:
        return f"no gaps found · {analysed} listings analysed"

    best = top[0]
    top_str = (
        f"{best['skill']} "
        f"({best['listings_blocked']} listings, "
        f"cost {best['opportunity_cost']})"
    )
    return (
        f"{len(top)} gaps · top: {top_str} · {analysed} listings analysed"
    )
