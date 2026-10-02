"""
app.py — EdgeDash entry point and page router.

Public:     Overview (the shared agent-activity dashboard), Log in / Sign up.
Logged in:  My Dashboard, My Profile, Tracked Jobs — all per-user.

Accounts are email + password, stored (hashed) in the app's own database;
no external identity provider or extra secrets are needed.

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
from edgedash.ui import bootstrap, current_user, log_out

cfg = bootstrap()
user = current_user(cfg.db_path)

overview = st.Page("views/overview.py", title="Overview", icon="📡", url_path="overview")

if user is None:
    account = st.Page("views/account.py", title="Log in / Sign up", icon="🔐", url_path="account")
    pages = [overview, account]
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
        st.caption("Logged in as")
        st.markdown(f"**{user['name']}**  \n{user['email']}")
        st.button("Log out", on_click=log_out, width="stretch")
    else:
        st.markdown("**Your own job matches**")
        st.caption("Create a free account to set your skills and get personal "
                   "scores, skill gaps and a job tracker.")
        st.page_link(account, label="Log in / Sign up", icon="🔐")

nav = st.navigation(pages)

# First login: send the user to onboarding before the dashboard. The
# profile page sets _has_profile on save, so this costs one query per session.
# Compare by title: Streamlit reports the default page's url_path as "", so
# the previous `nav.url_path == "dashboard"` check never fired.
if user is not None and nav.title == "My Dashboard" and not st.session_state.get("_has_profile"):
    if storage.get_profile(cfg.db_path, user["id"]) is None:
        st.switch_page("views/profile.py")
    st.session_state["_has_profile"] = True

nav.run()
