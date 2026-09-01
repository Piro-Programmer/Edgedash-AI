import pytest
from edgedash.query.tools import TOOLS, companies_hiring
from edgedash import storage

def test_tool_decorator():
    """Test that the @tool decorator registers the tool with the correct metadata."""
    assert "companies_hiring" in TOOLS
    meta = TOOLS["companies_hiring"]
    assert meta["name"] == "companies_hiring"
    assert "days" in meta["parameters"]["properties"]
    assert meta["func"] == companies_hiring.__wrapped__


def test_companies_hiring_clamping(tmp_path):
    """Test that days parameter is clamped between 1 and 90."""
    db = str(tmp_path / "test.db")
    storage.init_db(db)
    
    # Must have a passing cycle to return data (Rule 46)
    storage.log_cycle(db, "Orchestrator", "2026-08-01T00:00:00", "2026-08-01T00:01:00", 0, "complete", "")
    
    # We will patch storage.get_companies_hiring to observe what `days` is passed.
    observed_days = None
    
    def fake_get_companies(path, days):
        nonlocal observed_days
        observed_days = days
        return [{"company": "Test", "count": 5}]
        
    original_get = storage.get_companies_hiring
    storage.get_companies_hiring = fake_get_companies
    
    try:
        # Test lower bound (<= 0 clamps to 1)
        res, summary = companies_hiring(db, days=-5)
        assert observed_days == 1
        
        # Test string casting (from model output)
        res, summary = companies_hiring(db, days="45")
        assert observed_days == 45
        
        # Test upper bound (> 90 clamps to 90)
        res, summary = companies_hiring(db, days=150)
        assert observed_days == 90
        
        assert len(res) == 1
        assert "Found 5 listings across 1 companies" in summary
    finally:
        storage.get_companies_hiring = original_get


def test_companies_hiring_no_passing_cycle(tmp_path):
    """Test that it returns empty if there is no passing cycle."""
    db = str(tmp_path / "test.db")
    storage.init_db(db)
    
    # db is empty, no passing cycle
    res, summary = companies_hiring(db, days=7)
    assert res == []
    assert "No verified cycle data available" in summary

from edgedash.query.tools import (
    best_matches,
    top_gaps,
    gap_detail,
    trend,
    listing_count,
    skill_demand,
)

def test_best_matches_clamping(tmp_path):
    db = str(tmp_path / "test.db")
    storage.init_db(db)
    storage.log_cycle(db, "Orchestrator", "2026-08-01T00:00:00", "2026-08-01T00:01:00", 0, "complete", "")
    
    observed_n = None
    def fake_get_listings(path, limit, min_score=None):
        nonlocal observed_n
        observed_n = limit
        return []
        
    original = storage.get_listings
    storage.get_listings = fake_get_listings
    try:
        best_matches(db, n=-5)
        assert observed_n == 1
        best_matches(db, n=100)
        assert observed_n == 25
    finally:
        storage.get_listings = original


def test_top_gaps_clamping(tmp_path):
    db = str(tmp_path / "test.db")
    storage.init_db(db)
    storage.log_cycle(db, "Orchestrator", "2026-08-01T00:00:00", "2026-08-01T00:01:00", 0, "complete", "")
    
    def fake_get_latest_snapshot(path):
        # Return more than 25 items to test slicing
        return [{"skill": f"s{i}", "opportunity_cost": i, "listings_blocked": 1} for i in range(30)]
        
    original = storage.get_latest_snapshot
    storage.get_latest_snapshot = fake_get_latest_snapshot
    try:
        res, _ = top_gaps(db, n=-5)
        assert len(res) == 1
        res, _ = top_gaps(db, n=100)
        assert len(res) == 25
    finally:
        storage.get_latest_snapshot = original


def test_trend_clamping(tmp_path):
    db = str(tmp_path / "test.db")
    storage.init_db(db)
    storage.log_cycle(db, "Orchestrator", "2026-08-01T00:00:00", "2026-08-01T00:01:00", 0, "complete", "")
    
    observed_weeks = None
    def fake_get_trend(path, weeks):
        nonlocal observed_weeks
        observed_weeks = weeks
        return []
        
    original = storage.get_trend
    storage.get_trend = fake_get_trend
    try:
        trend(db, weeks=-5)
        assert observed_weeks == 1
        trend(db, weeks=50)
        assert observed_weeks == 12
    finally:
        storage.get_trend = original


def test_unknown_skill_returns_empty(tmp_path):
    db = str(tmp_path / "test.db")
    storage.init_db(db)
    storage.log_cycle(db, "Orchestrator", "2026-08-01T00:00:00", "2026-08-01T00:01:00", 0, "complete", "")
    
    # gap_detail for unknown skill should return empty, not raise
    res, summary = gap_detail(db, skill="nonexistent_skill")
    assert res == []
    assert "No listings found" in summary
    
    # skill_demand for unknown skill should return empty, not raise
    res, summary = skill_demand(db, skill="another_unknown")
    assert res == []
    assert "not found" in summary


def test_listing_count_shape(tmp_path):
    db = str(tmp_path / "test.db")
    storage.init_db(db)
    storage.log_cycle(db, "Orchestrator", "2026-08-01T00:00:00", "2026-08-01T00:01:00", 0, "complete", "")
    
    def fake_counts(path):
        return {"total": 100, "scored": 50, "unscored": 50, "newest_listing": "2026-08-01T00:00:00"}
        
    original = storage.get_listing_counts
    storage.get_listing_counts = fake_counts
    try:
        res, summary = listing_count(db)
        assert len(res) == 1
        assert res[0]["total"] == 100
        assert "Total listings: 100" in summary
    finally:
        storage.get_listing_counts = original
