"""
auth.py — email + password accounts (no third-party identity provider).

Pure functions only — no Streamlit, no database. storage.py persists the
hash; ui.py owns the session.

Passwords are hashed with PBKDF2-HMAC-SHA256 (stdlib, no extra dependency),
a random 16-byte salt per user and 600k iterations (OWASP 2023 guidance).
The stored string is self-describing so the cost can be raised later
without invalidating existing hashes:

    pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128   # bounds hashing cost for absurdly long inputs

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_email(email: str) -> str | None:
    """Return an error message, or None if the email looks valid."""
    email = normalize_email(email)
    if not email:
        return "Enter your email."
    if len(email) > 254 or not _EMAIL_RE.match(email):
        return "That doesn't look like a valid email address."
    return None


def validate_password(password: str) -> str | None:
    """Return an error message, or None if the password is acceptable."""
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Password must be at most {MAX_PASSWORD_LENGTH} characters."
    if password.isdigit() or password.isalpha():
        return "Use a mix of letters and numbers or symbols."
    return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"{_ALGO}${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of *password* against a stored hash string."""
    if not stored or not password or len(password) > MAX_PASSWORD_LENGTH:
        return False
    try:
        algo, iterations, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


# A real hash used to spend the same time when the email is unknown, so
# response timing does not reveal which emails have accounts.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def burn_verify_time(password: str) -> None:
    verify_password(password, _DUMMY_HASH)
