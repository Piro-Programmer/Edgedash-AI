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


@pytest.fixture
def base_env(tmp_path, monkeypatch):
    """Seeded DB with bootstrap() pointed at it; no fake login."""
    db = str(tmp_path / "views.db")
    _seed(db)
    cfg = Config(target_role="Engineer", target_city="Berlin", keywords=[],
                 my_skills=["python"], experience_years=2, db_path=db, min_fit_score=0)
    monkeypatch.setattr(ui, "bootstrap", lambda: cfg)
    ui.listings_with_facts.clear()
    ui._scored_for.clear()
    return cfg


@pytest.fixture
def app_env(base_env, monkeypatch):
    """base_env plus a fake logged-in USER for rendering per-user pages."""
    monkeypatch.setattr(ui, "require_user", lambda db_path: USER)
    return base_env


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
    storage.set_tracked_status(app_env.db_path, "someone-else", "other-listing", "offer")

    at = _run("views/tracked.py")
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["📨 Applied"] == "1"
    assert metrics["🎉 Offer"] == "0"


def test_logged_out_router_shows_overview_and_signup_link(base_env):
    at = _run("app.py")
    assert at.title[0].value == "EdgeDash"
    assert any("Create a free account" in c.value for c in at.sidebar.caption)


def _click(at: AppTest, label: str) -> AppTest:
    [btn] = [b for b in at.button if b.label == label]
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _fill(at: AppTest, **fields: str) -> None:
    for key, value in fields.items():
        at.text_input(key=key).input(value)


def test_signup_logout_login_flow(base_env):
    db = base_env.db_path
    at = _run("app.py")
    at.switch_page("views/account.py").run()

    # Sign up → logged in → sent to onboarding (no profile yet).
    _fill(at, signup_name="Pat Doe", signup_email="Pat@Example.com",
          signup_password="s3cret-pass", signup_confirm="s3cret-pass")
    _click(at, "Create account")
    assert at.session_state["user"]["email"] == "pat@example.com"
    assert at.title[0].value == "My Profile"

    row = storage.get_user_for_login(db, "pat@example.com")
    assert row["password_hash"].startswith("pbkdf2_sha256$")
    assert "s3cret-pass" not in row["password_hash"]

    # Log out clears the session.
    _click(at, "Log out")
    assert "user" not in at.session_state

    # Wrong password is rejected with a generic message.
    at.switch_page("views/account.py").run()
    _fill(at, login_email="pat@example.com", login_password="wrong-pass1")
    _click(at, "Log in")
    assert "user" not in at.session_state
    assert any("Incorrect email or password" in e.value for e in at.error)

    # Correct password logs in.
    _fill(at, login_email="PAT@example.com", login_password="s3cret-pass")
    _click(at, "Log in")
    assert at.session_state["user"]["id"] == row["id"]


def test_signup_rejects_duplicate_email_and_bad_input(base_env):
    storage.create_user(base_env.db_path, "taken@example.com", "T", "hash")
    at = _run("app.py")
    at.switch_page("views/account.py").run()

    _fill(at, signup_name="X", signup_email="taken@example.com",
          signup_password="s3cret-pass", signup_confirm="s3cret-pass")
    _click(at, "Create account")
    assert any("already exists" in e.value for e in at.error)

    _fill(at, signup_email="new@example.com", signup_confirm="different-pass1")
    _click(at, "Create account")
    assert any("don't match" in e.value for e in at.error)
    assert "user" not in at.session_state


def test_login_lockout_after_repeated_failures(base_env):
    at = _run("app.py")
    at.switch_page("views/account.py").run()
    for _ in range(5):
        _fill(at, login_email="ghost@example.com", login_password="nope-nope1")
        _click(at, "Log in")
    _fill(at, login_email="ghost@example.com", login_password="nope-nope1")
    _click(at, "Log in")
    assert any("Too many attempts" in e.value for e in at.error)


def test_protected_page_prompts_login_when_logged_out(base_env):
    at = _run("views/dashboard.py")
    assert any("Log in or create" in i.value for i in at.info)


def test_tracked_page_empty_state(app_env):
    at = _run("views/tracked.py")
    assert any("not tracking" in i.value for i in at.info)
