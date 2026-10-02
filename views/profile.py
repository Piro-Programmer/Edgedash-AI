"""
views/profile.py — the signed-in user's matching profile (also onboarding).

Everything here feeds edgedash.personal: skills, seniority, city and the
four scoring weights. Saving bumps updated_at, which invalidates the user's
cached scores so the dashboard reflects the change on the next view.
"""

from __future__ import annotations

import streamlit as st

import edgedash.storage as storage
from edgedash import personal
from edgedash.ui import bootstrap, listings_with_facts, load_profile, panel_error, require_user

cfg = bootstrap()
user = require_user(cfg.db_path)
profile, is_saved = load_profile(cfg, user["id"])

st.title("My Profile")
if not is_saved:
    st.info(
        f"Welcome, {user['name']}! Set up your profile to get personal job "
        "scores. We've pre-filled the site defaults — change anything that "
        "doesn't fit you, then save."
    )

try:
    suggestions = personal.popular_skills(listings_with_facts(cfg.db_path), cfg.skill_aliases)
except Exception as exc:
    panel_error("Skill suggestions", exc)
    suggestions = []

current_skills = list(profile.get("my_skills") or [])
# Options must contain the current values or the multiselect drops them.
options = list(dict.fromkeys(current_skills + suggestions))

weights = profile.get("weights") or {}
seniority = profile.get("target_seniority") or cfg.target_seniority
if seniority not in personal.SENIORITY_LEVELS:
    seniority = cfg.target_seniority

with st.form("profile_form"):
    st.subheader("What you bring")
    skills = st.multiselect(
        "Your skills",
        options=options,
        default=current_skills,
        accept_new_options=True,
        help="Pick from skills that real listings ask for, or type your own and press Enter.",
        placeholder="e.g. python, sql, kubernetes",
    )

    st.subheader("What you're looking for")
    c1, c2, c3 = st.columns(3)
    target_role = c1.text_input("Target role", value=profile.get("target_role") or "")
    target_city = c2.text_input(
        "City", value=profile.get("target_city") or "",
        help="Listings in this city score full marks on location. Remote listings always do.",
    )
    target_seniority = c3.selectbox(
        "Seniority", personal.SENIORITY_LEVELS,
        index=personal.SENIORITY_LEVELS.index(seniority),
    )
    min_fit_score = st.slider(
        "Show me matches scoring at least", 0, 100,
        int(profile.get("min_fit_score") or cfg.min_fit_score),
    )

    with st.expander("Scoring weights (advanced)"):
        st.caption("How much each factor counts. Weights are rescaled to add up to 100%.")
        w1, w2, w3, w4 = st.columns(4)
        new_weights = {
            "weight_skill_match": w1.slider("Skills", 0.0, 1.0,
                float(weights.get("weight_skill_match", cfg.weight_skill_match)), 0.05),
            "weight_seniority_fit": w2.slider("Seniority", 0.0, 1.0,
                float(weights.get("weight_seniority_fit", cfg.weight_seniority_fit)), 0.05),
            "weight_location_fit": w3.slider("Location", 0.0, 1.0,
                float(weights.get("weight_location_fit", cfg.weight_location_fit)), 0.05),
            "weight_recency": w4.slider("Recency", 0.0, 1.0,
                float(weights.get("weight_recency", cfg.weight_recency)), 0.05),
        }

    submitted = st.form_submit_button("Save profile", type="primary")

if submitted:
    cleaned = [s.strip().lower() for s in skills if s and s.strip()]
    if not cleaned:
        st.error("Add at least one skill — scores are mostly driven by skill match.")
    elif sum(new_weights.values()) == 0:
        st.error("At least one scoring weight must be above zero.")
    else:
        try:
            storage.save_profile(cfg.db_path, user["id"], {
                "my_skills": list(dict.fromkeys(cleaned)),
                "target_role": target_role.strip(),
                "target_city": target_city.strip(),
                "target_seniority": target_seniority,
                "weights": new_weights,
                "min_fit_score": min_fit_score,
            })
        except Exception as exc:
            panel_error("Saving profile", exc)
        else:
            st.session_state["_has_profile"] = True
            st.toast("Profile saved — your scores are updated.", icon="✅")
            st.switch_page("views/dashboard.py")
