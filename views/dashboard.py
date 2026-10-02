"""
views/dashboard.py — the signed-in user's personal job dashboard.

Scores, gaps and charts are computed for THIS user from cached extraction
facts (edgedash.personal) — no LLM calls happen on page load.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import altair as alt
import pandas as pd
import streamlit as st

import edgedash.storage as storage
from edgedash import personal
from edgedash.ui import bootstrap, load_profile, panel_error, require_user, scored_for_user

cfg = bootstrap()
user = require_user(cfg.db_path)
profile, _ = load_profile(cfg, user["id"])
user_cfg = personal.profile_to_config(profile, cfg)

first_name = (user["name"] or "").split(" ")[0] or "there"
st.title(f"Hi {first_name} 👋")
st.caption(
    f"Matches for **{profile.get('target_role') or 'your target role'}** · "
    f"{user_cfg.target_seniority} · {user_cfg.target_city or 'any city'} · "
    f"{len(user_cfg.my_skills)} skills on your profile"
)

try:
    scored = scored_for_user(cfg, user["id"], profile)
    tracked_rows = storage.list_tracked(cfg.db_path, user["id"])
except Exception as exc:
    panel_error("Your matches", exc)
    st.stop()

if not scored:
    st.info(
        "No analysed listings yet. The daily cycle extracts job details "
        "in the background — check back after the next run."
    )
    st.stop()

tracked_ids = {r["listing_id"] for r in tracked_rows}

# ---------------------------------------------------------------------------
# Build one DataFrame for everything below
# ---------------------------------------------------------------------------
df = pd.DataFrame([
    {
        "id": r["id"],
        "Score": r["fit_score"],
        "Title": r.get("title") or "—",
        "Company": r.get("company") or "—",
        "Location": r.get("location") or "—",
        "Remote": bool((r.get("facts") or {}).get("remote_ok")),
        "Posted": pd.to_datetime(r.get("posted_at") or r.get("fetched_at"), utc=True, errors="coerce"),
        "Why": r.get("fit_reason") or "",
        "Link": r.get("url"),
        "Tracked": r["id"] in tracked_ids,
    }
    for r in scored
])
by_id = {r["id"]: r for r in scored}

# ---------------------------------------------------------------------------
# Filters (sidebar)
# ---------------------------------------------------------------------------
with st.sidebar:
    st.subheader("Filters")
    # Start at 0, not the user's min score: a strict min on a thin listing
    # pool opened the page on an empty table. Rows are sorted best-first, and
    # the min score still drives the KPI and the "strong match" highlight.
    score_range = st.slider("Score", 0, 100, (0, 100))
    companies = st.multiselect("Company", sorted(df["Company"].unique()))
    days = st.select_slider("Posted within", options=[7, 14, 30, 60, 90, 365],
                            value=365, format_func=lambda d: f"{d} days")
    remote_only = st.toggle("Remote only")
    hide_tracked = st.toggle("Hide jobs I'm tracking")
    search = st.text_input("Search title", placeholder="e.g. data, backend")

view = df[df["Score"].between(*score_range)]
if companies:
    view = view[view["Company"].isin(companies)]
cutoff = datetime.now(timezone.utc) - timedelta(days=days)
view = view[view["Posted"].isna() | (view["Posted"] >= cutoff)]
if remote_only:
    view = view[view["Remote"]]
if hide_tracked:
    view = view[~view["Tracked"]]
if search.strip():
    view = view[view["Title"].str.contains(search.strip(), case=False, regex=False)]

gaps = personal.gaps_for_user(scored, user_cfg)

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
k1, k2, k3, k4 = st.columns(4)
k1.metric("Matches ≥ your min score", int((df["Score"] >= user_cfg.min_fit_score).sum()),
          help=f"Out of {len(df)} analysed listings.")
k2.metric("Best score", int(df["Score"].max()))
k3.metric("Jobs tracked", len(tracked_ids))
k4.metric("Top skill to learn", gaps[0]["skill"] if gaps else "—",
          help="The missing skill that blocks the most high-scoring jobs.")

tab_matches, tab_gaps, tab_market = st.tabs(["🎯 Top matches", "🧩 Skill gaps", "📈 Market"])

# ---------------------------------------------------------------------------
# Matches
# ---------------------------------------------------------------------------
with tab_matches:
    st.caption(f"{len(view)} of {len(df)} listings match your filters. "
               "Select rows to track them or see the score breakdown.")
    shown = view.reset_index(drop=True)
    event = st.dataframe(
        shown.drop(columns=["id", "Remote"]),
        hide_index=True,
        width="stretch",
        on_select="rerun",
        selection_mode="multi-row",
        key="matches_table",
        column_config={
            "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
            "Posted": st.column_config.DatetimeColumn("Posted", format="MMM D"),
            "Why": st.column_config.TextColumn("Why", width="large"),
            "Link": st.column_config.LinkColumn("Link", display_text="Open ↗"),
            "Tracked": st.column_config.CheckboxColumn("Tracked"),
        },
    )
    picked = [shown.loc[i, "id"] for i in event.selection.rows]

    best = int(df["Score"].max())
    if best < user_cfg.min_fit_score:
        st.caption(
            f"💡 Your best match scores {best}, below your minimum of "
            f"{user_cfg.min_fit_score}. Adding skills from the **Skill gaps** tab "
            "to your profile is the fastest way to raise it."
        )

    if picked:
        new = [lid for lid in picked if lid not in tracked_ids]
        if st.button(f"📌 Track {len(new)} selected job(s)", disabled=not new, type="primary"):
            try:
                for lid in new:
                    storage.set_tracked_status(cfg.db_path, user["id"], lid, "saved")
            except Exception as exc:
                panel_error("Tracking jobs", exc)
            else:
                st.toast(f"Tracking {len(new)} job(s). See them under Tracked Jobs.", icon="📌")
                st.rerun()

        for lid in picked[:3]:
            row = by_id[lid]
            comp = row["components"]
            detail = comp["skill_detail"]
            with st.expander(f"Score breakdown — {row.get('title')} @ {row.get('company')} ({row['fit_score']})",
                             expanded=len(picked) == 1):
                parts = pd.DataFrame({
                    "Factor": ["Skills", "Seniority", "Location", "Recency"],
                    "Fit": [comp["skill_match"], comp["seniority_fit"],
                            comp["location_fit"], comp["recency"]],
                    "Weight": [user_cfg.weight_skill_match, user_cfg.weight_seniority_fit,
                               user_cfg.weight_location_fit, user_cfg.weight_recency],
                })
                parts["Points"] = (parts["Fit"] * parts["Weight"] * 100).round(1)
                st.altair_chart(
                    alt.Chart(parts).mark_bar().encode(
                        x=alt.X("Points:Q", title="Points contributed"),
                        y=alt.Y("Factor:N", sort=None, title=None),
                        tooltip=["Factor", alt.Tooltip("Fit:Q", format=".0%"),
                                 alt.Tooltip("Weight:Q", format=".0%"), "Points"],
                    ).properties(height=160),
                    width="stretch",
                )
                st.markdown(
                    f"**Required skills you have:** {detail['matched_required']}/{detail['total_required']}"
                )
                if detail["missing_skills"]:
                    st.markdown("**Missing:** " + ", ".join(f"`{s}`" for s in detail["missing_skills"]))

# ---------------------------------------------------------------------------
# Skill gaps
# ---------------------------------------------------------------------------
with tab_gaps:
    if not gaps:
        st.success("No missing required skills across your analysed matches. 🎉")
    else:
        st.caption("Missing skills ranked by opportunity cost: how much fit score "
                   "they hold back across all listings that require them.")
        gdf = pd.DataFrame(gaps)
        st.altair_chart(
            alt.Chart(gdf).mark_bar().encode(
                x=alt.X("opportunity_cost:Q", title="Opportunity cost"),
                y=alt.Y("skill:N", sort="-x", title=None),
                color=alt.condition(alt.datum.low_confidence, alt.value("#9ca3af"), alt.value("#6366f1")),
                tooltip=[
                    alt.Tooltip("skill:N", title="Skill"),
                    alt.Tooltip("listings_blocked:Q", title="Jobs requiring it"),
                    alt.Tooltip("mean_score:Q", title="Mean score of those jobs"),
                    alt.Tooltip("top_score:Q", title="Best of those jobs"),
                ],
            ).properties(height=34 * len(gdf) + 20),
            width="stretch",
        )
        st.caption("Grey bars: fewer than 3 listings — low confidence.")

        skill = st.selectbox("See jobs that need…", [g["skill"] for g in gaps])
        g = next(x for x in gaps if x["skill"] == skill)
        blocked = [by_id[i] for i in g["example_ids"] if i in by_id]
        st.dataframe(
            pd.DataFrame([{"Score": r["fit_score"], "Title": r.get("title"),
                           "Company": r.get("company"), "Link": r.get("url")} for r in blocked]),
            hide_index=True, width="stretch",
            column_config={
                "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d"),
                "Link": st.column_config.LinkColumn("Link", display_text="Open ↗"),
            },
        )
        if skill not in user_cfg.my_skills:
            st.caption(f"Already know **{skill}**? Add it on My Profile and your scores update instantly.")

# ---------------------------------------------------------------------------
# Market charts (over the filtered view)
# ---------------------------------------------------------------------------
with tab_market:
    if view.empty:
        st.caption("No listings match your filters.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Your score distribution**")
            st.altair_chart(
                alt.Chart(view).mark_bar().encode(
                    x=alt.X("Score:Q", bin=alt.Bin(step=10), title="Fit score"),
                    y=alt.Y("count():Q", title="Listings"),
                    tooltip=[alt.Tooltip("count():Q", title="Listings")],
                ).properties(height=260),
                width="stretch",
            )
        with c2:
            st.markdown("**Top hiring companies (your filters)**")
            top_co = (view.groupby("Company").agg(Listings=("id", "count"), Best=("Score", "max"))
                      .reset_index().sort_values("Listings", ascending=False).head(10))
            st.altair_chart(
                alt.Chart(top_co).mark_bar().encode(
                    x=alt.X("Listings:Q"),
                    y=alt.Y("Company:N", sort="-x", title=None),
                    color=alt.Color("Best:Q", scale=alt.Scale(scheme="blues"), title="Best score"),
                    tooltip=["Company", "Listings", "Best"],
                ).properties(height=260),
                width="stretch",
            )

        st.markdown("**Listings posted per week**")
        weekly = view.dropna(subset=["Posted"]).copy()
        if weekly.empty:
            st.caption("No posting dates available.")
        else:
            weekly["Week"] = weekly["Posted"].dt.tz_convert(None).dt.to_period("W").dt.start_time
            weekly["Strong match"] = weekly["Score"] >= user_cfg.min_fit_score
            trend = weekly.groupby(["Week", "Strong match"]).size().reset_index(name="Listings")
            st.altair_chart(
                alt.Chart(trend).mark_bar().encode(
                    x=alt.X("Week:T", title=None),
                    y=alt.Y("Listings:Q", stack=True),
                    color=alt.Color("Strong match:N",
                                    scale=alt.Scale(domain=[True, False], range=["#6366f1", "#cbd5e1"]),
                                    legend=alt.Legend(title=f"Score ≥ {user_cfg.min_fit_score}")),
                    tooltip=["Week:T", "Listings:Q", "Strong match:N"],
                ).properties(height=240),
                width="stretch",
            )
