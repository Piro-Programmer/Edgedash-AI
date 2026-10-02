"""
ui.py — Streamlit glue shared by app.py and every page under views/.

Holds the config/DB bootstrap, the panel error helper, authentication and
the cached per-user data loaders. Pages import from here instead of each
re-implementing setup, so there is one place that decides who the user is.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Any

import streamlit as st

import edgedash.storage as storage
from edgedash import auth, personal
from edgedash.config import Config, load_config


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

@st.cache_data(ttl=10)
def _load_config() -> Config | None:
    try:
        return load_config()
    except FileNotFoundError:
        return None


@st.cache_resource
def _ensure_schema(db_path: str) -> str:
    """Create any missing tables once per process (CREATE TABLE IF NOT EXISTS)."""
    storage.init_db(db_path)
    return db_path


def bootstrap() -> Config:
    """Load config and make sure the schema exists, or stop the page."""
    cfg = _load_config()
    if cfg is None:
        st.error("config.yaml not found. Run EdgeDash from the project root.")
        st.stop()
    try:
        _ensure_schema(cfg.db_path)
    except Exception as exc:
        logging.error("Database connection failed: %s", exc, exc_info=True)
        st.error(
            f"Cannot reach the database ({type(exc).__name__}: {exc}). "
            "Check the DATABASE_URL secret in your Streamlit Cloud app settings."
        )
        st.stop()
    return cfg


def panel_error(label: str, exc: Exception) -> None:
    """Show a panel-level failure without hiding what actually went wrong."""
    logging.error("%s failed: %s", label, exc, exc_info=True)
    st.error(f"{label} unavailable — {type(exc).__name__}: {exc}")
    with st.expander("Show technical details"):
        st.code("".join(traceback.format_exception(exc)), language="text")


# ---------------------------------------------------------------------------
# Authentication (email + password, session-scoped)
#
# st.session_state["user"] is set ONLY by sign_up() / log_in() after the
# password checks out, and its "id" is the ONLY value pages may pass as
# user_id to storage. A browser refresh starts a new Streamlit session, so
# users log in again after reloading the page.
# ---------------------------------------------------------------------------

_SESSION_KEY = "user"
_MAX_FAILED_LOGINS = 5
_LOCKOUT_SECONDS = 60


def current_user(db_path: str | None = None) -> dict[str, str] | None:
    """The logged-in user for this browser session, or None."""
    return st.session_state.get(_SESSION_KEY)


def _start_session(user_id: str, email: str, name: str) -> None:
    st.session_state[_SESSION_KEY] = {"id": user_id, "email": email, "name": name}
    st.session_state.pop("_has_profile", None)
    st.session_state.pop("_failed_logins", None)


def log_out() -> None:
    # Drop everything tied to the account, not just the user key, so the next
    # person on this browser tab starts clean.
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def sign_up(db_path: str, name: str, email: str, password: str, confirm: str) -> str | None:
    """Create an account and log in. Returns an error message, or None."""
    email = auth.normalize_email(email)
    name = (name or "").strip()
    if not name:
        return "Enter your name."
    if len(name) > 80:
        return "Name must be at most 80 characters."
    error = auth.validate_email(email) or auth.validate_password(password)
    if error:
        return error
    if password != confirm:
        return "Passwords don't match."
    try:
        user_id = storage.create_user(db_path, email, name, auth.hash_password(password))
    except storage.EmailTakenError:
        return "An account with this email already exists. Log in instead."
    _start_session(user_id, email, name)
    return None


def log_in(db_path: str, email: str, password: str) -> str | None:
    """Check credentials and start a session. Returns an error message, or None."""
    locked_until = st.session_state.get("_locked_until", 0)
    if time.time() < locked_until:
        return f"Too many attempts. Try again in {int(locked_until - time.time()) + 1}s."

    email = auth.normalize_email(email)
    row = storage.get_user_for_login(db_path, email) if email else None
    if row and auth.verify_password(password, row.get("password_hash")):
        storage.record_login(db_path, row["id"])
        _start_session(row["id"], row["email"], row.get("name") or row["email"])
        return None

    if not row:
        auth.burn_verify_time(password or "x")   # same timing as a wrong password
    fails = st.session_state.get("_failed_logins", 0) + 1
    st.session_state["_failed_logins"] = fails
    if fails >= _MAX_FAILED_LOGINS:
        st.session_state["_locked_until"] = time.time() + _LOCKOUT_SECONDS
        st.session_state["_failed_logins"] = 0
    # Same message whether the email exists or not.
    return "Incorrect email or password."


def require_user(db_path: str) -> dict[str, str]:
    """Return the logged-in user or stop the page with a login prompt."""
    user = current_user(db_path)
    if user is None:
        st.info("Log in or create a free account to see this page.")
        try:
            st.page_link("views/account.py", label="Log in / Sign up", icon="🔐")
        except Exception:
            # page_link only resolves pages registered in the current
            # navigation; fall back to plain guidance rather than crash.
            st.caption("Use **Log in / Sign up** in the sidebar.")
        st.stop()
    return user


# ---------------------------------------------------------------------------
# Per-user data
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner=False)
def listings_with_facts(db_path: str) -> list[dict[str, Any]]:
    """Shared across users: listings + extracted facts (no user data)."""
    return storage.get_listings_with_facts(db_path)


def load_profile(cfg: Config, user_id: str) -> tuple[dict[str, Any], bool]:
    """(profile, is_saved). Unsaved users get the site default as a draft."""
    saved = storage.get_profile(cfg.db_path, user_id)
    if saved:
        return saved, True
    return personal.default_profile(cfg), False


@st.cache_data(ttl=300, show_spinner=False)
def _scored_for(db_path: str, user_id: str, profile_version: str,
                _profile: dict[str, Any], _cfg: Config) -> list[dict[str, Any]]:
    # Cache key is (db_path, user_id, profile_version); the underscored args
    # are excluded from hashing. Saving the profile bumps updated_at, which
    # invalidates this entry immediately.
    user_cfg = personal.profile_to_config(_profile, _cfg)
    return personal.score_for_user(listings_with_facts(db_path), user_cfg)


def scored_for_user(cfg: Config, user_id: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    version = str(profile.get("updated_at") or "default")
    return _scored_for(cfg.db_path, user_id, version, profile, cfg)
