"""
verification.py — deterministic checks that catch failure modes.

No LLM calls. Pure functions only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from edgedash.config import Config


@dataclass
class CheckResult:
    name: str
    passed: bool
    observed: float | int | str
    threshold: float | int | str
    message: str


@dataclass
class Verdict:
    passed: bool
    failed_checks: list[CheckResult]
    summary: str


def check_score_spread(scores: list[int], config: Config) -> CheckResult:
    """FAILS if score spread or stdev is too small (inflation/compression)."""
    if len(scores) < 5:
        return CheckResult(
            name="check_score_spread",
            passed=True,
            observed=len(scores),
            threshold=5,
            message="Passed trivially (fewer than 5 scores)."
        )

    spread = max(scores) - min(scores)
    mean = sum(scores) / len(scores)
    variance = sum((x - mean) ** 2 for x in scores) / len(scores)
    stdev = variance ** 0.5

    if spread < config.min_score_spread:
        return CheckResult(
            name="check_score_spread",
            passed=False,
            observed=spread,
            threshold=config.min_score_spread,
            message=f"Score spread ({spread}) is below threshold ({config.min_score_spread})."
        )

    if stdev < config.min_score_stdev:
        return CheckResult(
            name="check_score_spread",
            passed=False,
            observed=stdev,
            threshold=config.min_score_stdev,
            message=f"Score stdev ({stdev:.2f}) is below threshold ({config.min_score_stdev})."
        )

    return CheckResult(
        name="check_score_spread",
        passed=True,
        observed=spread,
        threshold=config.min_score_spread,
        message=f"Spread ({spread}) and stdev ({stdev:.2f}) are healthy."
    )


def check_extraction_sanity(facts_list: list[dict[str, Any]], config: Config) -> CheckResult:
    """FAILS if too many empty extractions or any listing has too many skills."""
    if not facts_list:
        return CheckResult(
            name="check_extraction_sanity",
            passed=True,
            observed=0,
            threshold=0,
            message="No facts to check."
        )

    empty_count = sum(1 for f in facts_list if not f.get("required_skills"))
    empty_pct = (empty_count / len(facts_list)) * 100

    if empty_pct > config.max_empty_extraction_pct:
        return CheckResult(
            name="check_extraction_sanity",
            passed=False,
            observed=empty_pct,
            threshold=config.max_empty_extraction_pct,
            message=f"Too many empty extractions ({empty_pct:.1f}% > {config.max_empty_extraction_pct}%)."
        )

    for facts in facts_list:
        num_skills = len(facts.get("required_skills") or [])
        if num_skills > config.max_skills_per_listing:
            return CheckResult(
                name="check_extraction_sanity",
                passed=False,
                observed=num_skills,
                threshold=config.max_skills_per_listing,
                message=f"Listing has too many skills ({num_skills} > {config.max_skills_per_listing})."
            )

    return CheckResult(
        name="check_extraction_sanity",
        passed=True,
        observed=empty_pct,
        threshold=config.max_empty_extraction_pct,
        message=f"Extractions healthy ({empty_pct:.1f}% empty, max skills within limits)."
    )


def check_gap_sample_size(gaps: list[dict[str, Any]], config: Config) -> CheckResult:
    """FAILS if the top-ranked gap is computed from too few listings."""
    if not gaps:
        return CheckResult(
            name="check_gap_sample_size",
            passed=True,
            observed=0,
            threshold=config.min_gap_sample,
            message="No gaps to check."
        )

    top_gap = gaps[0]
    sample = top_gap.get("sample_size", top_gap.get("listings_blocked", 0))
    
    if sample < config.min_gap_sample:
        return CheckResult(
            name="check_gap_sample_size",
            passed=False,
            observed=sample,
            threshold=config.min_gap_sample,
            message=f"Top gap computed from too few listings ({sample} < {config.min_gap_sample})."
        )

    return CheckResult(
        name="check_gap_sample_size",
        passed=True,
        observed=sample,
        threshold=config.min_gap_sample,
        message=f"Top gap has healthy sample size ({sample} >= {config.min_gap_sample})."
    )


def check_freshness(latest_fetch_at: str | None, config: Config, now: datetime) -> CheckResult:
    """FAILS if the newest listing is older than max_data_age_days."""
    if not latest_fetch_at:
        return CheckResult(
            name="check_freshness",
            passed=True,
            observed="None",
            threshold=config.max_data_age_days,
            message="No fetches yet."
        )

    # Parse ISO 8601 string to datetime
    try:
        latest = datetime.fromisoformat(latest_fetch_at)
    except ValueError:
        return CheckResult(
            name="check_freshness",
            passed=False,
            observed="Invalid format",
            threshold=config.max_data_age_days,
            message=f"Could not parse latest_fetch_at: {latest_fetch_at}"
        )

    age_days = (now - latest).total_seconds() / 86400

    if age_days > config.max_data_age_days:
        return CheckResult(
            name="check_freshness",
            passed=False,
            observed=age_days,
            threshold=config.max_data_age_days,
            message=f"Data is too stale ({age_days:.1f} days > {config.max_data_age_days})."
        )

    return CheckResult(
        name="check_freshness",
        passed=True,
        observed=age_days,
        threshold=config.max_data_age_days,
        message=f"Data is fresh ({age_days:.1f} days <= {config.max_data_age_days})."
    )


def run_all_checks(
    scores: list[int],
    facts_list: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
    latest_fetch_at: str | None,
    config: Config,
    now: datetime
) -> Verdict:
    """Runs all checks and returns a combined Verdict."""
    checks = [
        check_score_spread(scores, config),
        check_extraction_sanity(facts_list, config),
        check_gap_sample_size(gaps, config),
        check_freshness(latest_fetch_at, config, now),
    ]

    failed = [c for c in checks if not c.passed]
    passed = len(failed) == 0

    if passed:
        summary = f"All {len(checks)} checks passed."
    else:
        summary = f"{len(failed)}/{len(checks)} checks failed."

    return Verdict(
        passed=passed,
        failed_checks=failed,
        summary=summary
    )
