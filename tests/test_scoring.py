from datetime import datetime, timezone

from edgedash.config import Config
from edgedash.scoring import score_listing


def get_base_config() -> Config:
    return Config(
        target_role="Software Engineer",
        target_city="London",
        keywords=[],
        my_skills=["python", "pytest", "sql", "docker"],
        experience_years=3,
        db_path=":memory:",
        min_fit_score=0,
        target_seniority="mid",
        weight_skill_match=0.45,
        weight_seniority_fit=0.25,
        weight_location_fit=0.15,
        weight_recency=0.15,
    )


def test_perfect_match():
    config = get_base_config()
    listing = {
        "location": "London",
        "posted_at": datetime.now(timezone.utc).isoformat()
    }
    facts = {
        "required_skills": ["python", "sql"],
        "nice_to_have": ["docker"],
        "seniority": "mid",
        "remote_ok": True
    }
    
    res = score_listing(listing, facts, config)
    assert res["score"] == 100
    assert res["components"]["skill_match"] == 1.0
    assert res["components"]["seniority_fit"] == 1.0
    assert res["components"]["location_fit"] == 1.0
    assert res["components"]["recency"] == 1.0


def test_zero_match():
    config = get_base_config()
    config.target_seniority = "junior"
    
    listing = {
        "location": "Tokyo",
        "posted_at": "2020-01-01T00:00:00Z"
    }
    facts = {
        "required_skills": ["java", "c++"],
        "nice_to_have": ["kubernetes"],
        "seniority": "lead",
        "remote_ok": False
    }
    
    res = score_listing(listing, facts, config)
    assert res["components"]["skill_match"] == 0.0
    assert res["components"]["seniority_fit"] == 0.0
    assert res["components"]["location_fit"] == 0.1
    assert res["components"]["recency"] == 0.0
    
    assert res["score"] == 2  # 0.1 * 0.15 * 100 = 1.5, rounded to 2


def test_empty_required_skills():
    config = get_base_config()
    listing = {"location": "London"}
    facts = {
        "required_skills": [],
        "nice_to_have": [],
        "seniority": "mid",
    }
    res = score_listing(listing, facts, config)
    assert res["components"]["skill_match"] == 0.5


def test_null_posted_at():
    config = get_base_config()
    listing = {"posted_at": None}
    facts = {}
    res = score_listing(listing, facts, config)
    assert res["components"]["recency"] == 0.5


def test_null_remote_ok():
    config = get_base_config()
    
    # Location doesn't match and remote is null -> 0.5
    listing_unknown = {"location": "Tokyo"}
    facts_unknown = {"remote_ok": None}
    res1 = score_listing(listing_unknown, facts_unknown, config)
    assert res1["components"]["location_fit"] == 0.5
    
    # Location matches but remote is null -> 1.0
    listing_match = {"location": "London"}
    res2 = score_listing(listing_match, facts_unknown, config)
    assert res2["components"]["location_fit"] == 1.0


def test_seniority_three_bands_off():
    config = get_base_config()
    config.target_seniority = "junior"
    listing = {}
    facts = {"seniority": "lead"}
    res = score_listing(listing, facts, config)
    assert res["components"]["seniority_fit"] == 0.0
