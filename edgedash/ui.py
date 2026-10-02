"""
ui.py — Streamlit glue shared by app.py and every page under views/.

Holds the config/DB bootstrap, the panel error helper, authentication and
the cached per-user data loaders. Pages import from here instead of each
re-implementing setup, so there is one place that decides who the user is.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

import streamlit as st

import edgedash.storage as storage
from edgedash.config import Config, load_config
from edgedash import personal


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
# Authentication (Streamlit native OIDC — st.login / st.user / st.logout)
# ---------------------------------------------------------------------------

def auth_configured() -> bool:
    """True when an [auth] block with a Google provider is in secrets."""
    try:
        auth = st.secrets.get("auth")
    except Exception:  # no secrets.toml at all
        return False
    return bool(auth) and "google" in auth


def current_user(db_path: str) -> dict[str, str] | None:
    """The signed-in user, or None. Records the login once per session.

    The user id is the OIDC subject (Google's stable account id). It is the
    ONLY value pages may pass as user_id to storage — never anything taken
    from widgets or query params.
    """
    if not auth_configured() or not st.user.is_logged_in:
        return None
    user = {
        "id": str(st.user.get("sub")),
        "email": st.user.get("email") or "",
        "name": st.user.get("name") or st.user.get("email") or "you",
    }
    if st.session_state.get("_recorded_login") != user["id"]:
        storage.upsert_user(db_path, user["id"], user["email"], user["name"])
        st.session_state["_recorded_login"] = user["id"]
    return user


def require_user(db_path: str) -> dict[str, str]:
    """Return the signed-in user or stop the page with a sign-in prompt."""
    user = current_user(db_path)
    if user is None:
        st.info("Sign in with Google to see this page.")
        if auth_configured():
            st.button("Continue with Google", on_click=st.login, args=("google",),
                      type="primary", key="require_login_btn")
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
