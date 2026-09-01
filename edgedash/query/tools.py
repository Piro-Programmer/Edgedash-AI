"""
Query tools registry. 

These tools provide parameterised, read-only access to the EdgeDash database.
They are exposed to the router LLM, but the execution and validation is entirely
deterministic Python (no LLM in this file).
"""

from __future__ import annotations

import functools
from typing import Any, Callable

from edgedash import storage

# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

TOOLS: dict[str, dict[str, Any]] = {}


def tool(description: str, params: dict[str, Any]) -> Callable:
    """Decorator to register a query tool.
    
    Registers the function's name, description, and JSON-schema parameters
    in the global TOOLS dictionary.
    """
    def decorator(func: Callable) -> Callable:
        TOOLS[func.__name__] = {
            "name": func.__name__,
            "description": description,
            "parameters": params,
            "func": func,
        }
        
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> tuple[list[dict], str]:
            return func(*args, **kwargs)
            
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool(
    description="Returns companies that have posted job listings in the last N days, sorted by the number of listings.",
    params={
        "type": "object",
        "properties": {
            "days": {
                "type": "integer",
                "description": "Number of days to look back (default 7).",
            }
        },
        "required": [],
    }
)
def companies_hiring(db_path: str, days: int = 7) -> tuple[list[dict], str]:
    """Companies with listings posted in the last N days, with counts."""
    # Rule 41: parameter clamping
    days = max(1, min(int(days), 90))
    
    # Rule 46: read from last passing cycle
    passing = storage.get_latest_passing_cycle(db_path)
    if not passing:
        return [], "No verified cycle data available."
        
    results = storage.get_companies_hiring(db_path, days)
    
    total_companies = len(results)
    total_listings = sum(r["count"] for r in results)
    
    summary = f"Found {total_listings} listings across {total_companies} companies in the last {days} days."
    return results, summary

@tool(
    description="Returns the highest-scoring listings with their fit score, title, company, and matching reason.",
    params={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of listings to return (default 10, max 25).",
            }
        },
        "required": [],
    }
)
def best_matches(db_path: str, n: int = 10) -> tuple[list[dict], str]:
    """Highest-scoring listings with score, title, company, reason."""
    n = max(1, min(int(n), 25))
    passing = storage.get_latest_passing_cycle(db_path)
    if not passing:
        return [], "No verified cycle data available."
        
    listings = storage.get_listings(db_path, limit=n)
    results = [
        {
            "score": l.get("fit_score"),
            "title": l.get("title"),
            "company": l.get("company"),
            "reason": l.get("fit_reason")
        } for l in listings if l.get("fit_score") is not None
    ]
    summary = f"Found the top {len(results)} highest-scoring listings."
    return results, summary


@tool(
    description="Returns the top skill gaps by opportunity cost, showing how many listings are blocked.",
    params={
        "type": "object",
        "properties": {
            "n": {
                "type": "integer",
                "description": "Number of top gaps to return (default 5, max 25).",
            }
        },
        "required": [],
    }
)
def top_gaps(db_path: str, n: int = 5) -> tuple[list[dict], str]:
    """Top skill gaps by opportunity cost, with listings_blocked."""
    n = max(1, min(int(n), 25))
    passing = storage.get_latest_passing_cycle(db_path)
    if not passing:
        return [], "No verified cycle data available."
        
    gaps = storage.get_latest_snapshot(db_path)
    if not gaps:
        return [], "No gap snapshots available."
        
    gaps.sort(key=lambda g: g.get("opportunity_cost", 0), reverse=True)
    results = [
        {
            "skill": g.get("skill"),
            "opportunity_cost": g.get("opportunity_cost"),
            "listings_blocked": g.get("listings_blocked")
        } for g in gaps[:n]
    ]
    summary = f"Found the top {len(results)} skill gaps."
    return results, summary


@tool(
    description="Returns details on listings blocked by a specific skill.",
    params={
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The skill name to query.",
            }
        },
        "required": ["skill"],
    }
)
def gap_detail(db_path: str, skill: str) -> tuple[list[dict], str]:
    """The listings blocked by one named skill."""
    from edgedash.skills import canonical
    passing = storage.get_latest_passing_cycle(db_path)
    if not passing:
        return [], "No verified cycle data available."
        
    from edgedash.config import load_config; skill_canon = canonical(skill, load_config().skill_aliases)
    results = storage.get_gap_detail(db_path, skill_canon)
    if not results:
        return [], f"No listings found blocked by skill '{skill_canon}'."
        
    summary = f"Found {len(results)} listings blocked by '{skill_canon}'."
    return results, summary


@tool(
    description="Returns the opportunity cost change of skill gaps over N weeks.",
    params={
        "type": "object",
        "properties": {
            "weeks": {
                "type": "integer",
                "description": "Number of weeks to look back (default 3, max 12).",
            }
        },
        "required": [],
    }
)
def trend(db_path: str, weeks: int = 3) -> tuple[list[dict], str]:
    """Gap opportunity_cost change over N weeks from the snapshots."""
    weeks = max(1, min(int(weeks), 12))
    passing = storage.get_latest_passing_cycle(db_path)
    if not passing:
        return [], "No verified cycle data available."
        
    data = storage.get_trend(db_path, weeks)
    
    # Calculate change: latest - oldest
    skill_series: dict[str, list[dict]] = {}
    for row in data:
        s = row["skill"]
        if s not in skill_series:
            skill_series[s] = []
        skill_series[s].append(row)
        
    results = []
    for s, rows in skill_series.items():
        if len(rows) > 1:
            oldest = rows[0]["opportunity_cost"]
            latest = rows[-1]["opportunity_cost"]
            change = latest - oldest
            results.append({
                "skill": s,
                "oldest_cost": oldest,
                "latest_cost": latest,
                "change": change
            })
    
    # Sort by absolute change
    results.sort(key=lambda r: abs(r["change"]), reverse=True)
    
    summary = f"Found trends for {len(results)} skills over the last {weeks} weeks."
    return results, summary


@tool(
    description="Returns overall listing totals: total listings, scored, unscored, and newest listing date.",
    params={
        "type": "object",
        "properties": {},
    }
)
def listing_count(db_path: str) -> tuple[list[dict], str]:
    """Totals: listings, scored, unscored, newest listing date."""
    passing = storage.get_latest_passing_cycle(db_path)
    if not passing:
        return [], "No verified cycle data available."
        
    counts = storage.get_listing_counts(db_path)
    summary = f"Total listings: {counts.get('total', 0)}, Scored: {counts.get('scored', 0)}."
    return [counts], summary


@tool(
    description="Returns how often a specific skill appears as required vs nice_to_have across all extracted listings.",
    params={
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The skill name to query.",
            }
        },
        "required": ["skill"],
    }
)
def skill_demand(db_path: str, skill: str) -> tuple[list[dict], str]:
    """How often one skill appears in required vs nice_to_have."""
    from edgedash.skills import canonical
    passing = storage.get_latest_passing_cycle(db_path)
    if not passing:
        return [], "No verified cycle data available."
        
    from edgedash.config import load_config; skill_canon = canonical(skill, load_config().skill_aliases)
    demand = storage.get_skill_demand(db_path, skill_canon)
    
    total = demand.get("required", 0) + demand.get("nice_to_have", 0)
    if total == 0:
        return [], f"Skill '{skill_canon}' not found in the database."
        
    summary = f"Skill '{skill_canon}' appears {demand.get('required', 0)} times as required and {demand.get('nice_to_have', 0)} times as nice-to-have."
    demand["skill"] = skill_canon
    return [demand], summary
