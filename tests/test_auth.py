"""Password hashing and input validation (edgedash/auth.py)."""

from __future__ import annotations

import pytest

from edgedash import auth


def test_hash_round_trip_and_salting():
    h1 = auth.hash_password("correct horse 1")
    h2 = auth.hash_password("correct horse 1")
    assert h1 != h2                       # random salt per hash
    assert h1.startswith("pbkdf2_sha256$")
    assert "correct horse" not in h1      # never stores the plaintext
    assert auth.verify_password("correct horse 1", h1)
    assert not auth.verify_password("correct horse 2", h1)


@pytest.mark.parametrize("stored", [None, "", "garbage", "md5$1$aa$bb", "pbkdf2_sha256$x$zz$yy"])
def test_verify_rejects_malformed_hashes(stored):
    assert not auth.verify_password("anything1", stored)


def test_verify_rejects_overlong_password_without_hashing():
    h = auth.hash_password("short-pass1")
    assert not auth.verify_password("a" * (auth.MAX_PASSWORD_LENGTH + 1), h)


@pytest.mark.parametrize("email,ok", [
    ("dev@example.com", True),
    ("  Dev@Example.COM ", True),
    ("", False),
    ("no-at-sign.com", False),
    ("a@b", False),
    ("two@@example.com", False),
])
def test_validate_email(email, ok):
    assert (auth.validate_email(email) is None) is ok


def test_normalize_email():
    assert auth.normalize_email("  Dev@Example.COM ") == "dev@example.com"


@pytest.mark.parametrize("password,ok", [
    ("abc12345", True),
    ("pass word!", True),
    ("short1", False),          # too short
    ("12345678", False),        # digits only
    ("abcdefgh", False),        # letters only
    ("a1" * 100, False),        # too long
])
def test_validate_password(password, ok):
    assert (auth.validate_password(password) is None) is ok
