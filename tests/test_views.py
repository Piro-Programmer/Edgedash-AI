"""
Render every page headlessly with Streamlit's AppTest against a seeded
SQLite DB. Auth is faked by patching edgedash.ui — the pages import
require_user/bootstrap at script run time, so the patch takes effect.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

import edgedash.storage as storage
import edgedash.ui as ui
from edgedash.config import Config

USER = {"id": "google-sub-123", "email": "dev@example.com", "name": "Dev User"}


def _seed(db: str) -> None:
    storage.init_db(db)
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for i, (title, skills, remote) in enumerate([
        ("Python Engineer", ["python", "sql"], True),
        ("Go Engineer", ["go", "kubernetes"], False),
        ("Data Engineer", ["python", "spark", "sql"], True),
    ]):
        desc = f"description {i}"
        rows.append({"source": "t", "url": f"https://x/{i}", "title": title, "company": f"Co{i % 2}",
                     "location": "Berlin", "description": desc, "posted_at": now})
        storage.set_extraction_cache(db, hashlib.sha256(desc.encode()).hexdigest(), {
            "required_skills": skills, "nice_to_have": [], "seniority": "mid", "remote_ok": remote,
        })
    storage.upsert_listings(db, rows)
    storage.upsert_user(db, USER["id"], USER["email"], USER["name"])


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    db = str(tmp_path / "views.db")
    _seed(db)
    cfg = Config(target_role="Engineer", target_city="Berlin", keywords=[],
                 my_skills=["python"], experience_years=2, db_path=db, min_fit_score=0)
    monkeypatch.setattr(ui, "bootstrap", lambda: cfg)
    monkeypatch.setattr(ui, "require_user", lambda db_path: USER)
    ui.listings_with_facts.clear()
    ui._scored_for.clear()
    return cfg


def _run(path: str) -> AppTest:
    at = AppTest.from_file(path, default_timeout=30)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_dashboard_renders_personal_matches(app_env):
    storage.save_profile(app_env.db_path, USER["id"], {
        "my_skills": ["python", "sql"], "target_city": "Berlin", "target_seniority": "mid",
        "weights": {}, "min_fit_score": 0,
    })
    at = _run("views/dashboard.py")
    assert "Hi Dev" in at.title[0].value
    assert len(at.dataframe) >= 1
    assert at.metric[0].value == "3"   # all three seeded listings score >= 0


def test_profile_page_onboards_new_user(app_env):
    at = _run("views/profile.py")
    assert any("Welcome" in i.value for i in at.info)
    assert at.multiselect[0].value == ["python"]   # pre-filled from config defaults


def test_tracked_page_lists_only_this_users_jobs(app_env):
    lid = storage.get_listings_with_facts(app_env.db_path)[0]["id"]
    storage.set_tracked_status(app_env.db_path, USER["id"], lid, "applied")
    storage.upsert_user(app_env.db_path, "someone-else", None, None)
    storage.set_tracked_status(app_env.db_path, "someone-else", "other-listing", "offer")

    at = _run("views/tracked.py")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["📨 Applied"] == "1"
    assert metrics["🎉 Offer"] == "0"


def test_logged_out_router_shows_only_public_overview(app_env, monkeypatch):
    monkeypatch.setattr(ui, "current_user", lambda db_path: None)
    at = _run("app.py")
    assert at.title[0].value == "EdgeDash"
    assert any("not configured" in c.value for c in at.sidebar.caption)


def test_tracked_page_empty_state(app_env):
    at = _run("views/tracked.py")
    assert any("not tracking" in i.value for i in at.info)
