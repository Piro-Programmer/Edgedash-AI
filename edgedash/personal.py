"""
personal.py — per-user scoring and skill gaps, computed on the fly.

No LLM calls. Extraction is user-independent and already cached by the
scheduled cycle, so a user's view is pure arithmetic over cached facts:
scoring.score_listing() with that user's profile swapped into the Config,
and the GapAnalyzer's own _compute_gaps() over the result.

Public API
----------
    default_profile(config) -> dict
    profile_to_config(profile, base) -> Config
    score_for_user(listings, config) -> list[dict]
    gaps_for_user(scored, config, top_n=10) -> list[dict]
"""

from __future__ import annotations

import dataclasses
from typing import Any

from edgedash.agents.gap_analyzer import _compute_gaps
from edgedash.config import Config
from edgedash.scoring import score_listing
from edgedash.skills import canonical

SENIORITY_LEVELS: tuple[str, ...] = ("junior", "mid", "senior", "lead")

WEIGHT_KEYS: tuple[str, ...] = (
    "weight_skill_match",
    "weight_seniority_fit",
    "weight_location_fit",
    "weight_recency",
)


def default_profile(config: Config) -> dict[str, Any]:
    """The site-wide config.yaml profile, as a starting point for a new user."""
    return {
        "my_skills": list(config.my_skills),
        "target_role": config.target_role,
        "target_city": config.target_city,
        "target_seniority": config.target_seniority,
        "weights": {k: getattr(config, k) for k in WEIGHT_KEYS},
        "min_fit_score": config.min_fit_score,
    }


def profile_to_config(profile: dict[str, Any], base: Config) -> Config:
    """Return a copy of *base* with the user's profile fields applied.

    Weights are normalised to sum to 1.0 so a user dragging every slider up
    cannot push scores past 100 or flatten them all to the same value.
    """
    weights = {k: float((profile.get("weights") or {}).get(k, getattr(base, k)))
               for k in WEIGHT_KEYS}
    total = sum(max(0.0, w) for w in weights.values())
    if total > 0:
        weights = {k: max(0.0, w) / total for k, w in weights.items()}
    else:
        weights = {k: getattr(base, k) for k in WEIGHT_KEYS}

    seniority = (profile.get("target_seniority") or base.target_seniority).lower()
    if seniority not in SENIORITY_LEVELS:
        seniority = base.target_seniority

    return dataclasses.replace(
        base,
        my_skills=[s.strip() for s in profile.get("my_skills") or [] if s and s.strip()],
        target_role=profile.get("target_role") or base.target_role,
        target_city=profile.get("target_city") or "",
        target_seniority=seniority,
        min_fit_score=int(profile.get("min_fit_score", base.min_fit_score)),
        **weights,
    )


def score_for_user(listings: list[dict[str, Any]], config: Config) -> list[dict[str, Any]]:
    """Score every listing that has facts; highest score first.

    Each returned row is a shallow copy of the listing plus fit_score,
    fit_reason and components — the input rows are shared across users
    through the Streamlit cache and must not be mutated.
    """
    scored: list[dict[str, Any]] = []
    for listing in listings:
        facts = listing.get("facts")
        if not facts:
            continue
        result = score_listing(listing, facts, config)
        row = dict(listing)
        row["fit_score"] = result["score"]
        row["fit_reason"] = result["reason"]
        row["components"] = result["components"]
        scored.append(row)
    scored.sort(key=lambda r: r["fit_score"], reverse=True)
    return scored


def gaps_for_user(
    scored: list[dict[str, Any]],
    config: Config,
    top_n: int = 10,
) -> list[dict[str, Any]]:
    """Rank the user's missing skills by opportunity cost (same maths as the
    GapAnalyzer agent, but against this user's skills and scores)."""
    if not scored:
        return []
    my_skills = {canonical(s, config.skill_aliases) for s in config.my_skills}
    gaps = _compute_gaps(scored, my_skills, config.skill_aliases)
    ranked = sorted(gaps.values(), key=lambda g: g["opportunity_cost"], reverse=True)
    return ranked[:top_n]


def popular_skills(listings: list[dict[str, Any]], aliases: dict[str, str], limit: int = 150) -> list[str]:
    """Most frequently required skills across extracted listings — used as
    suggestions in the profile form."""
    counts: dict[str, int] = {}
    for listing in listings:
        facts = listing.get("facts") or {}
        for raw in (facts.get("required_skills") or []) + (facts.get("nice_to_have") or []):
            skill = canonical(raw, aliases)
            if skill:
                counts[skill] = counts.get(skill, 0) + 1
    return [s for s, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]]
