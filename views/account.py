"""
views/account.py — log in / sign up with email and password.
"""

from __future__ import annotations

import streamlit as st

from edgedash.auth import MIN_PASSWORD_LENGTH
from edgedash.ui import bootstrap, current_user, log_in, panel_error, sign_up

cfg = bootstrap()

if current_user(cfg.db_path) is not None:
    st.switch_page("views/dashboard.py")

_, center, _ = st.columns([1, 2, 1])
with center:
    st.title("Welcome to EdgeDash")
    st.caption("Get job matches scored against your own skills, see which skills "
               "would unlock the most jobs, and track your applications.")

    tab_login, tab_signup = st.tabs(["Log in", "Create account"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email", autocomplete="email", key="login_email")
            password = st.text_input("Password", type="password",
                                     autocomplete="current-password", key="login_password")
            submitted = st.form_submit_button("Log in", type="primary", width="stretch")
        if submitted:
            try:
                error = log_in(cfg.db_path, email, password)
            except Exception as exc:
                panel_error("Login", exc)
            else:
                if error:
                    st.error(error)
                else:
                    st.rerun()

    with tab_signup:
        with st.form("signup_form"):
            name = st.text_input("Name", autocomplete="name", key="signup_name")
            new_email = st.text_input("Email", autocomplete="email", key="signup_email")
            new_password = st.text_input(
                "Password", type="password", autocomplete="new-password", key="signup_password",
                help=f"At least {MIN_PASSWORD_LENGTH} characters, mixing letters with numbers or symbols.",
            )
            confirm = st.text_input("Confirm password", type="password",
                                    autocomplete="new-password", key="signup_confirm")
            created = st.form_submit_button("Create account", type="primary", width="stretch")
        if created:
            try:
                error = sign_up(cfg.db_path, name, new_email, new_password, confirm)
            except Exception as exc:
                panel_error("Sign up", exc)
            else:
                if error:
                    st.error(error)
                else:
                    st.rerun()
