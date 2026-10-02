"""Per-user scoring must match the shared scorer exactly for the same profile."""

from __future__ import annotations

from edgedash.config import Config
from edgedash.personal import (
    default_profile,
    gaps_for_user,
    popular_skills,
    profile_to_config,
    score_for_user,
)
from edgedash.scoring import score_listing


def _base() -> Config:
    return Config(
        target_role="Software Engineer",
        target_city="London",
        keywords=[],
        my_skills=["python"],
        experience_years=3,
        db_path=":memory:",
        min_fit_score=50,
    )


def _listings() -> list[dict]:
    return [
        {"id": "a", "title": "Py dev", "location": "London", "posted_at": None,
         "facts": {"required_skills": ["python", "sql"], "nice_to_have": [],
                   "seniority": "mid", "remote_ok": None}},
        {"id": "b", "title": "Go dev", "location": "Paris", "posted_at": None,
         "facts": {"required_skills": ["go", "kubernetes"], "nice_to_have": ["sql"],
                   "seniority": "senior", "remote_ok": False}},
        {"id": "c", "title": "No facts", "location": "London", "posted_at": None, "facts": None},
    ]


def test_default_profile_reproduces_shared_scores():
    base = _base()
    cfg = profile_to_config(default_profile(base), base)
    scored = score_for_user(_listings(), cfg)
    for row in scored:
        expected = score_listing(row, row["facts"], base)["score"]
        assert row["fit_score"] == expected


def test_listings_without_facts_are_skipped_and_order_is_descending():
    base = _base()
    scored = score_for_user(_listings(), base)
    assert [r["id"] for r in scored] == ["a", "b"]
    assert scored[0]["fit_score"] >= scored[1]["fit_score"]


def test_input_rows_are_not_mutated():
    rows = _listings()
    score_for_user(rows, _base())
    assert "fit_score" not in rows[0]


def test_changing_skills_changes_scores():
    base = _base()
    go_cfg = profile_to_config({**default_profile(base), "my_skills": ["go", "kubernetes"]}, base)
    scored = {r["id"]: r["fit_score"] for r in score_for_user(_listings(), go_cfg)}
    py = {r["id"]: r["fit_score"] for r in score_for_user(_listings(), base)}
    assert scored["b"] > py["b"]
    assert scored["a"] < py["a"]


def test_weights_are_normalised():
    base = _base()
    cfg = profile_to_config({**default_profile(base), "weights": {
        "weight_skill_match": 5, "weight_seniority_fit": 5,
        "weight_location_fit": 5, "weight_recency": 5}}, base)
    total = (cfg.weight_skill_match + cfg.weight_seniority_fit
             + cfg.weight_location_fit + cfg.weight_recency)
    assert abs(total - 1.0) < 1e-9
    assert all(0 <= r["fit_score"] <= 100 for r in score_for_user(_listings(), cfg))


def test_invalid_seniority_falls_back():
    base = _base()
    cfg = profile_to_config({**default_profile(base), "target_seniority": "wizard"}, base)
    assert cfg.target_seniority == base.target_seniority


def test_gaps_reflect_user_skills():
    base = _base()
    gaps = {g["skill"] for g in gaps_for_user(score_for_user(_listings(), base), base)}
    assert "python" not in gaps
    assert {"sql", "go", "kubernetes"} <= gaps

    sql_cfg = profile_to_config({**default_profile(base), "my_skills": ["python", "sql"]}, base)
    gaps2 = {g["skill"] for g in gaps_for_user(score_for_user(_listings(), sql_cfg), sql_cfg)}
    assert "sql" not in gaps2


def test_popular_skills_ranked_by_frequency():
    assert popular_skills(_listings(), {})[0] == "sql"
