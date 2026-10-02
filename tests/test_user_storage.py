"""Per-user storage: profiles and tracked jobs, and isolation between users."""

from __future__ import annotations

import hashlib

import pytest

import edgedash.storage as storage


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "users.db")
    storage.init_db(path)
    storage.upsert_user(path, "alice", "alice@example.com", "Alice")
    storage.upsert_user(path, "bob", "bob@example.com", "Bob")
    return path


_PROFILE = {
    "my_skills": ["python", "sql"],
    "target_role": "Data Engineer",
    "target_city": "Berlin",
    "target_seniority": "senior",
    "weights": {"weight_skill_match": 0.7, "weight_seniority_fit": 0.1,
                "weight_location_fit": 0.1, "weight_recency": 0.1},
    "min_fit_score": 40,
}


def test_upsert_user_is_idempotent(db):
    storage.upsert_user(db, "alice", "new@example.com", "Alice B")
    with storage._connect(db) as conn:
        rows = conn.execute("SELECT * FROM users WHERE id = ?", ("alice",)).fetchall()
    assert len(rows) == 1
    assert rows[0]["email"] == "new@example.com"


def test_profile_round_trip(db):
    assert storage.get_profile(db, "alice") is None
    storage.save_profile(db, "alice", _PROFILE)
    got = storage.get_profile(db, "alice")
    assert got["my_skills"] == ["python", "sql"]
    assert got["weights"]["weight_skill_match"] == 0.7
    assert got["target_seniority"] == "senior"
    assert got["min_fit_score"] == 40


def test_profile_update_overwrites(db):
    storage.save_profile(db, "alice", _PROFILE)
    storage.save_profile(db, "alice", {**_PROFILE, "my_skills": ["go"]})
    assert storage.get_profile(db, "alice")["my_skills"] == ["go"]


def test_profiles_are_isolated(db):
    storage.save_profile(db, "alice", _PROFILE)
    assert storage.get_profile(db, "bob") is None


def test_tracked_jobs_round_trip_and_isolation(db):
    storage.set_tracked_status(db, "alice", "L1", "saved")
    storage.set_tracked_status(db, "alice", "L1", "applied", notes="sent CV")
    storage.set_tracked_status(db, "bob", "L2", "interview")

    alice = storage.list_tracked(db, "alice")
    bob = storage.list_tracked(db, "bob")
    assert [(r["listing_id"], r["status"], r["notes"]) for r in alice] == [("L1", "applied", "sent CV")]
    assert [r["listing_id"] for r in bob] == ["L2"]

    # Bob removing "L1" must not touch Alice's row.
    storage.remove_tracked(db, "bob", "L1")
    assert len(storage.list_tracked(db, "alice")) == 1
    storage.remove_tracked(db, "alice", "L1")
    assert storage.list_tracked(db, "alice") == []


def test_invalid_tracked_status_rejected(db):
    with pytest.raises(ValueError):
        storage.set_tracked_status(db, "alice", "L1", "hired!")


def test_get_listings_with_facts_includes_unscored(db):
    rows = [
        {"source": "t", "url": "u1", "title": "A", "company": "C", "location": "Berlin",
         "description": "desc one", "posted_at": None},
        {"source": "t", "url": "u2", "title": "B", "company": "C", "location": "Berlin",
         "description": "desc two", "posted_at": None},
    ]
    storage.upsert_listings(db, rows)
    h = hashlib.sha256(b"desc one").hexdigest()
    storage.set_extraction_cache(db, h, {"required_skills": ["python"]})

    got = storage.get_listings_with_facts(db)
    assert [r["title"] for r in got] == ["A"]
    assert got[0]["facts"] == {"required_skills": ["python"]}
