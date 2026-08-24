"""
scoring.py — deterministic fit scoring (steering rules 16, 19).

NO model calls.  No network.  No imports from llm.py.
Pure functions only — every output is fully determined by its inputs.

Public API
----------
    score_listing(listing, facts, config) -> dict
    build_reason(components, facts, config) -> str
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from edgedash.config import Config

# ---------------------------------------------------------------------------
# Seniority band order — used for distance calculation
# ---------------------------------------------------------------------------

_SENIORITY_BANDS: list[str] = ["junior", "mid", "senior", "lead"]

# Score awarded per band distance (index 0 = exact match)
_SENIORITY_SCORES: list[float] = [1.0, 0.6, 0.25, 0.0]

_RECENCY_DECAY_DAYS: int = 30   # score reaches 0.0 at this age


# ---------------------------------------------------------------------------
# Component scorers  (each returns a float 0.0–1.0)
# ---------------------------------------------------------------------------

def _score_skill_match(facts: dict, my_skills: list[str]) -> tuple[float, dict]:
    """Fraction of required skills covered, with nice-to-have at 1/3 weight.

    Returns the component score and a detail dict used by build_reason.
    """
    my_set = {s.lower() for s in my_skills}

    required: list[str] = facts.get("required_skills") or []
    nice: list[str] = facts.get("nice_to_have") or []

    # Required-skills match — guard against empty list (no division by zero).
    if required:
        matched_req = [s for s in required if s.lower() in my_set]
        req_score = len(matched_req) / len(required)
    else:
        # No required skills stated → treat as a neutral 0.5; not a perfect
        # match and not a disqualifier.
        matched_req = []
        req_score = 0.5

    # Nice-to-have match — counts at 1/3 weight relative to required.
    if nice:
        matched_nice = [s for s in nice if s.lower() in my_set]
        nice_score = len(matched_nice) / len(nice)
    else:
        matched_nice = []
        nice_score = 0.0

    # Blend: required carries 3 parts, nice-to-have carries 1 part.
    if required and nice:
        blended = (3 * req_score + nice_score) / 4
    elif required:
        blended = req_score
    else:
        blended = req_score  # 0.5 sentinel

    missing = [s for s in required if s.lower() not in my_set]

    return blended, {
        "matched_required": len(matched_req),
        "total_required": len(required),
        "matched_nice": len(matched_nice),
        "total_nice": len(nice),
        "missing_skills": missing,
    }


def _score_seniority(facts: dict, target: str) -> float:
    """Band-distance score between the listing's seniority and the target."""
    listing_seniority: str = (facts.get("seniority") or "unknown").lower()
    target_lower = target.lower()

    if listing_seniority == "unknown":
        return 0.5   # no information → neutral

    try:
        listing_idx = _SENIORITY_BANDS.index(listing_seniority)
    except ValueError:
        return 0.5   # unrecognised value → neutral

    try:
        target_idx = _SENIORITY_BANDS.index(target_lower)
    except ValueError:
        return 0.5   # misconfigured target → neutral

    distance = abs(listing_idx - target_idx)
    # Clamp to the length of the score table.
    distance = min(distance, len(_SENIORITY_SCORES) - 1)
    return _SENIORITY_SCORES[distance]


def _score_location(listing: dict, facts: dict, target_city: str) -> float:
    """Remote-ok / city-match / unknown / elsewhere."""
    remote_ok: bool | None = facts.get("remote_ok")

    if remote_ok is True:
        return 1.0

    location: str = (listing.get("location") or "").lower()
    city_lower = target_city.lower()

    if city_lower and city_lower in location:
        return 1.0

    if remote_ok is None and not location:
        return 0.5   # nothing stated at all → neutral

    if remote_ok is None:
        # Location present but city doesn't match and remote not stated.
        return 0.5

    # remote_ok is False and city doesn't match → clearly elsewhere.
    return 0.1


def _score_recency(listing: dict) -> float:
    """Linear decay from 1.0 (today) to 0.0 at _RECENCY_DECAY_DAYS days."""
    posted_at: str | None = listing.get("posted_at")

    if not posted_at:
        return 0.5   # null → neutral, must not crash

    try:
        # Accept ISO-8601 with or without timezone.
        if posted_at.endswith("Z"):
            posted_at = posted_at[:-1] + "+00:00"
        dt = datetime.fromisoformat(posted_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - dt).total_seconds() / 86400
    except (ValueError, OverflowError):
        return 0.5   # unparseable date → neutral

    if age_days < 0:
        age_days = 0.0

    score = 1.0 - (age_days / _RECENCY_DECAY_DAYS)
    return max(0.0, min(1.0, score))


# ---------------------------------------------------------------------------
# Public: score_listing
# ---------------------------------------------------------------------------

def score_listing(
    listing: dict[str, Any],
    facts: dict[str, Any],
    config: Config,
) -> dict[str, Any]:
    """Compute a 0-100 fit score for *listing* using extracted *facts*.

    Returns
    -------
    {
        "score":      int,          # 0–100
        "reason":     str,          # human-readable, built from numbers
        "components": {             # raw 0.0–1.0 values before weighting
            "skill_match":    float,
            "seniority_fit":  float,
            "location_fit":   float,
            "recency":        float,
            "skill_detail":   dict,   # matched/total/missing breakdown
        }
    }
    """
    skill_score, skill_detail = _score_skill_match(facts, config.my_skills)
    seniority_score           = _score_seniority(facts, config.target_seniority)
    location_score            = _score_location(listing, facts, config.target_city)
    recency_score             = _score_recency(listing)

    weighted = (
        skill_score     * config.weight_skill_match
        + seniority_score * config.weight_seniority_fit
        + location_score  * config.weight_location_fit
        + recency_score   * config.weight_recency
    )

    # Clamp, then convert to integer 0-100.
    score = int(round(max(0.0, min(1.0, weighted)) * 100))

    components = {
        "skill_match":   skill_score,
        "seniority_fit": seniority_score,
        "location_fit":  location_score,
        "recency":       recency_score,
        "skill_detail":  skill_detail,
    }

    reason = build_reason(components, facts, config)

    return {"score": score, "reason": reason, "components": components}


# ---------------------------------------------------------------------------
# Public: build_reason  (rule 19 — generated from numbers, not by the model)
# ---------------------------------------------------------------------------

def build_reason(
    components: dict[str, Any],
    facts: dict[str, Any],
    config: Config,
) -> str:
    """Build a compact, human-readable reason string from score components.

    Example output:
        "4/6 required skills · seniority fits · remote · posted 2d ago · gap: kubernetes, spark"
    """
    parts: list[str] = []
    detail: dict = components.get("skill_detail", {})

    # ── skills ──────────────────────────────────────────────────────────────
    total_req = detail.get("total_required", 0)
    matched   = detail.get("matched_required", 0)

    if total_req == 0:
        parts.append("no required skills listed")
    elif matched == total_req:
        parts.append(f"all {total_req} required skills")
    else:
        parts.append(f"{matched}/{total_req} required skills")

    # ── seniority ────────────────────────────────────────────────────────────
    sen_score = components.get("seniority_fit", 0.0)
    listing_seniority = (facts.get("seniority") or "unknown").lower()
    if sen_score == 1.0:
        parts.append("seniority fits")
    elif listing_seniority == "unknown":
        parts.append("seniority unknown")
    else:
        parts.append(f"seniority mismatch ({listing_seniority} vs {config.target_seniority})")

    # ── location ─────────────────────────────────────────────────────────────
    loc_score = components.get("location_fit", 0.0)
    if loc_score == 1.0:
        remote_ok = facts.get("remote_ok")
        if remote_ok is True:
            parts.append("remote")
        else:
            parts.append(f"in {config.target_city}")
    elif loc_score >= 0.5:
        parts.append("location unclear")
    else:
        parts.append("wrong location")

    # ── recency ──────────────────────────────────────────────────────────────
    rec_score = components.get("recency", 0.5)
    if rec_score == 0.5:
        # Neutral sentinel — posted_at was null or unparseable.
        parts.append("posted_at unknown")
    elif rec_score >= 0.97:
        parts.append("posted today")
    else:
        days = max(1, int(round((1.0 - rec_score) * _RECENCY_DECAY_DAYS)))
        parts.append(f"posted {days}d ago")

    # ── skill gaps (the most actionable part) ────────────────────────────────
    missing: list[str] = detail.get("missing_skills", [])
    if missing:
        gap_str = ", ".join(missing[:5])   # cap at 5 to keep it readable
        if len(missing) > 5:
            gap_str += f" (+{len(missing) - 5} more)"
        parts.append(f"gap: {gap_str}")

    return " · ".join(parts)
