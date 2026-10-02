"""
app.py — EdgeDash entry point and page router.

Public:     Overview (the shared agent-activity dashboard).
Signed in:  My Dashboard, My Profile, Tracked Jobs — all per-user.

Sign-in uses Streamlit's native OIDC (st.login) with Google; configure it
in .streamlit/secrets.toml (see .streamlit/secrets.toml.example). Without
that block the site still works, just with the public Overview only.

Run:  python -m streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="EdgeDash",
    page_icon="⚡",
    layout="wide",
)

import edgedash.storage as storage
from edgedash.ui import auth_configured, bootstrap, current_user

cfg = bootstrap()
user = current_user(cfg.db_path)

overview = st.Page("views/overview.py", title="Overview", icon="📡", url_path="overview")

if user is None:
    pages = [overview]
else:
    dashboard = st.Page("views/dashboard.py", title="My Dashboard", icon="🎯",
                        url_path="dashboard", default=True)
    profile = st.Page("views/profile.py", title="My Profile", icon="🧑‍💻",
                      url_path="profile")
    tracked = st.Page("views/tracked.py", title="Tracked Jobs", icon="📌",
                      url_path="tracked")
    pages = {"You": [dashboard, profile, tracked], "Community": [overview]}

# ── Sidebar: account ─────────────────────────────────────────────────────────
with st.sidebar:
    if user is not None:
        st.caption("Signed in as")
        st.markdown(f"**{user['name']}**  \n{user['email']}")
        st.button("Log out", on_click=st.logout, width="stretch")
    elif auth_configured():
        st.markdown("**Your own job matches**")
        st.caption("Sign in to set your skills and get personal scores, "
                   "skill gaps and a job tracker.")
        st.button("Continue with Google", on_click=st.login, args=("google",),
                  type="primary", width="stretch")
    else:
        st.caption("Sign-in is not configured on this deployment.")

nav = st.navigation(pages)

# First sign-in: send the user to onboarding before the dashboard. The
# profile page sets _has_profile on save, so this costs one query per session.
if user is not None and nav.url_path == "dashboard" and not st.session_state.get("_has_profile"):
    if storage.get_profile(cfg.db_path, user["id"]) is None:
        st.switch_page("views/profile.py")
    st.session_state["_has_profile"] = True

nav.run()
