"""
Tests for verification.py
"""

from datetime import datetime, timezone, timedelta
from edgedash.config import Config
from edgedash.verification import (
    check_score_spread,
    check_extraction_sanity,
    check_gap_sample_size,
    check_freshness,
    run_all_checks
)

def get_base_config() -> Config:
    return Config(
        target_role="Software Engineer",
        target_city="London",
        keywords=[],
        my_skills=["python", "sql"],
        experience_years=3,
        db_path=":memory:",
        min_fit_score=0,
        min_score_spread=10,
        min_score_stdev=5.0,
        max_empty_extraction_pct=20.0,
        max_skills_per_listing=20,
        min_gap_sample=3,
        max_data_age_days=3,
    )

def test_check_score_spread_passing():
    config = get_base_config()
    scores = [10, 20, 30, 40, 50]  # spread = 40, stdev = 14.14
    res = check_score_spread(scores, config)
    assert res.passed

def test_check_score_spread_failing_spread():
    config = get_base_config()
    scores = [40, 42, 45, 43, 44]  # spread = 5 < 10
    res = check_score_spread(scores, config)
    assert not res.passed
    assert "spread" in res.message.lower()

def test_check_score_spread_failing_stdev():
    config = get_base_config()
    # spread >= 10, but stdev < 5
    # mean = 50. variance = (100+100) / 22 = 9.09. stdev = 3.01
    scores = [50] * 20 + [40, 60]
    res = check_score_spread(scores, config)
    assert not res.passed
    assert "stdev" in res.message.lower()

def test_check_score_spread_trivial():
    config = get_base_config()
    scores = [50, 50, 50, 50]  # len < 5
    res = check_score_spread(scores, config)
    assert res.passed
    assert "trivially" in res.message

def test_check_extraction_sanity_passing():
    config = get_base_config()
    facts_list = [
        {"required_skills": ["python"]},
        {"required_skills": ["sql", "docker"]},
    ]
    res = check_extraction_sanity(facts_list, config)
    assert res.passed

def test_check_extraction_sanity_failing_empty_pct():
    config = get_base_config()
    facts_list = [
        {"required_skills": ["python"]},
        {"required_skills": []},  # Empty
        {"required_skills": []},  # Empty
    ]
    # empty_pct = 66.6% > 20%
    res = check_extraction_sanity(facts_list, config)
    assert not res.passed
    assert "empty" in res.message.lower()

def test_check_extraction_sanity_failing_max_skills():
    config = get_base_config()
    facts_list = [
        {"required_skills": ["skill"] * 21},  # 21 > 20
    ]
    res = check_extraction_sanity(facts_list, config)
    assert not res.passed
    assert "too many skills" in res.message.lower()

def test_check_gap_sample_size_passing():
    config = get_base_config()
    gaps = [
        {"listings_blocked": 3, "skill": "python"},
        {"listings_blocked": 2, "skill": "sql"}
    ]
    res = check_gap_sample_size(gaps, config)
    assert res.passed

def test_check_gap_sample_size_failing():
    config = get_base_config()
    gaps = [
        {"listings_blocked": 2, "skill": "python"} # Top gap has 2 < 3
    ]
    res = check_gap_sample_size(gaps, config)
    assert not res.passed

def test_check_gap_sample_size_fallback_to_sample_size():
    config = get_base_config()
    gaps = [
        {"sample_size": 3, "skill": "python"}
    ]
    res = check_gap_sample_size(gaps, config)
    assert res.passed

def test_check_freshness_passing():
    config = get_base_config()
    now = datetime.now(timezone.utc)
    # 2 days old
    latest_fetch_at = (now - timedelta(days=2)).isoformat()
    res = check_freshness(latest_fetch_at, config, now)
    assert res.passed

def test_check_freshness_failing():
    config = get_base_config()
    now = datetime.now(timezone.utc)
    # 4 days old (max is 3)
    latest_fetch_at = (now - timedelta(days=4)).isoformat()
    res = check_freshness(latest_fetch_at, config, now)
    assert not res.passed

def test_run_all_checks():
    config = get_base_config()
    now = datetime.now(timezone.utc)
    
    scores = [10, 20, 30, 40, 50]
    facts_list = [{"required_skills": ["python"]}]
    gaps = [{"listings_blocked": 3, "skill": "python"}]
    latest_fetch_at = now.isoformat()
    
    verdict = run_all_checks(scores, facts_list, gaps, latest_fetch_at, config, now)
    assert verdict.passed
    assert len(verdict.failed_checks) == 0
