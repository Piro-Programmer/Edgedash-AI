"""
app.py — EdgeDash Agent Activity Dashboard (read-only).

Reads through the storage module ONLY. Never writes. Never runs a cycle.
Per rule 38, data panels read from the LAST PASSING CYCLE only.
The activity log is the exception — it shows ALL cycles including failures.

Run:  python -m streamlit run app.py
"""

from __future__ import annotations

import streamlit as st
import pandas as pd
from datetime import datetime
import logging

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="EdgeDash",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from edgedash.config import load_config
import edgedash.storage as storage

# ---------------------------------------------------------------------------
# Config & DB path
# ---------------------------------------------------------------------------
@st.cache_data(ttl=10)
def _load_config():
    try:
        return load_config()
    except FileNotFoundError:
        return None

cfg = _load_config()
if cfg is None:
    st.error("config.yaml not found. Run EdgeDash from the project root.")
    st.stop()

DB = cfg.db_path

try:
    with storage._connect(DB) as conn:
        pass
except Exception as e:
    logging.error(f"Database connection failed: {e}")
    st.error("Database not configured.")
    st.stop()

# ---------------------------------------------------------------------------
# Cached read functions
# ---------------------------------------------------------------------------
@st.cache_data(ttl=5)
def _get_latest_passing_cycle():
    return storage.get_latest_passing_cycle(DB)

@st.cache_data(ttl=5)
def _last_cycle_verdict():
    return storage.last_cycle_verdict(DB)

@st.cache_data(ttl=5)
def _get_recent_cycles(limit: int = 30):
    return storage.get_recent_cycles(DB, limit)

@st.cache_data(ttl=5)
def _get_listings(limit: int = 10, min_score: int = 0):
    return storage.get_listings(DB, limit=limit, min_score=min_score)

@st.cache_data(ttl=5)
def _get_latest_snapshot():
    return storage.get_latest_snapshot(DB)

@st.cache_data(ttl=5)
def _count_all_listings():
    try:
        with storage._connect(DB) as conn:
            total = conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
            scored = conn.execute(
                "SELECT COUNT(*) FROM listings WHERE fit_score IS NOT NULL"
            ).fetchone()[0]
        return total, scored
    except Exception:
        return 0, 0

# ---------------------------------------------------------------------------
# Custom CSS (minimal, to match requested design)
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Warning banner matching Image 1 */
    .warning-banner {
        background-color: #3f421f;
        color: #e5e7eb;
        padding: 16px 20px;
        border-radius: 6px;
        margin-top: 24px;
        margin-bottom: 24px;
        font-weight: 500;
        font-size: 1.05rem;
    }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# SECTION 1 — Header strip
# ---------------------------------------------------------------------------
st.title("EdgeDash")
st.caption("Read-only. The scheduler writes; this page only reads.")

try:
    latest_verdict_status, latest_verdict_at = _last_cycle_verdict()
    passing = _get_latest_passing_cycle()
    total_listings, scored_listings = _count_all_listings()

    if total_listings == 0 and not latest_verdict_at:
        st.info("No cycles yet — first run is scheduled shortly.")
        st.stop()
except Exception as e:
    logging.error(f"Failed to read header stats: {e}", exc_info=True)
    st.info("Database not fully initialized. First run is scheduled shortly.")
    st.stop()

st.markdown("<br/>", unsafe_allow_html=True)
col1, col2, col3, col4 = st.columns(4)

cycle_ts = "none"
if passing:
    cycle_ts = passing.get("started_at", "none")[:19].replace("T", " ")
elif latest_verdict_at:
    cycle_ts = latest_verdict_at[:19].replace("T", " ")

col1.metric("Last successful cycle", cycle_ts)
col2.metric("Total listings", str(total_listings))
col3.metric("Total scored", str(scored_listings))
col4.metric("Current verdict", str(latest_verdict_status or "none"))

# Warning banner logic
is_stale = False
hide_panels = False
if latest_verdict_status in ("failed", "degraded"):
    if not passing:
        hide_panels = True
        st.markdown(
            '<div class="warning-banner">'
            '<b>The newest cycle failed</b>, and there is no earlier verified cycle. Listing and gap panels are hidden.'
            '</div>', 
            unsafe_allow_html=True
        )
    else:
        is_stale = True
        st.markdown(
            '<div class="warning-banner">'
            '<b>The newest cycle failed.</b> Data below is from the last verified cycle. It may not reflect the current state.'
            '</div>', 
            unsafe_allow_html=True
        )


# ---------------------------------------------------------------------------
# SECTION 1.5 — Ask your data
# ---------------------------------------------------------------------------
st.markdown("<br/>", unsafe_allow_html=True)
st.subheader("Ask your data")
st.caption("Answers come from owned query tools, not free-form SQL.")

# Example questions as clickable buttons
col1, col2, col3 = st.columns(3)
example_q = None

if col1.button("Which companies posted jobs in the last 7 days?"):
    example_q = "Which companies posted jobs in the last 7 days?"
if col2.button("What are my top skill gaps?"):
    example_q = "What are my top skill gaps?"
if col3.button("How many listings are scored?"):
    example_q = "How many listings are scored?"

# Wrap in a form or use button state
user_q = st.text_input("Question", value=example_q if example_q else "")
submit = st.button("Ask")

if (submit or example_q) and user_q:
    try:
        with st.spinner("Routing query and analyzing..."):
            from edgedash.query.ask import ask
            answer = ask(user_q)
            
            # Display answer
            st.markdown(f"**Answer:** {answer.text}")
            
            # Display rows underneath in a table
            if answer.rows:
                st.dataframe(
                    pd.DataFrame(answer.rows),
                    use_container_width=True,
                    hide_index=True,
                )
            elif answer.tool_used:
                st.info("The query executed successfully but returned no rows.")
                
            st.caption(f"Used tool: `{answer.tool_used}` with params: `{answer.params}`")
    except Exception as e:
        logging.error(f"Ask panel error: {e}", exc_info=True)
        st.error("Error querying data. Please try again.")

# ---------------------------------------------------------------------------
# SECTION 2 — Agent Activity Log
# ---------------------------------------------------------------------------
st.markdown("<br/>", unsafe_allow_html=True)
st.subheader("Agent activity log")
st.caption("Most recent 30 cycles, including failed and degraded runs.")

try:
    cycles = _get_recent_cycles(30)

    def parse_cycle_row(row: dict) -> dict:
        started = row.get("started_at", "")
        finished = row.get("finished_at", "")
        ts = started[:19].replace("T", " ") + " UTC" if started else "—"
        
        duration = "—"
        if started and finished:
            try:
                t1 = datetime.fromisoformat(started)
                t2 = datetime.fromisoformat(finished)
                dur_sec = (t2 - t1).total_seconds()
                duration = f"{dur_sec:.1f}s"
            except Exception:
                pass

        notes = row.get("notes") or ""
        agents_run = []
        skipped = []
        failed_check = "—"
        retries = "—"
        
        for part in notes.split("|"):
            part = part.strip()
            if "status=" in part:
                agent = part.split(":")[0].strip()
                if agent not in ("cycle", "Orchestrator"):
                    agents_run.append(agent)
            elif "skipped" in part:
                agent = part.split(":")[0].strip()
                if agent not in ("cycle", "Orchestrator"):
                    skipped.append(agent)
            elif part.startswith("VERDICT:"):
                if "—" in part:
                    failed_check = part.split("—", 1)[1].strip()
                else:
                    failed_check = part.split(":", 1)[1].strip()
            elif part.startswith("retries="):
                retries = part.split("=")[1].strip()

        return {
            "Timestamp": ts,
            "Agents run": ", ".join(agents_run) if agents_run else "—",
            "Skipped": ", ".join(skipped) if skipped else "—",
            "Verdict": row.get("status", "—"),
            "Failed check": failed_check,
            "Retries": retries,
            "Duration": duration,
        }

    if not cycles:
        st.info("No cycle data yet.")
    else:
        df = pd.DataFrame([parse_cycle_row(r) for r in cycles])
        
        def highlight_rows(row):
            # Apply dark red background to degraded rows
            if row["Verdict"] in ("degraded", "failed"):
                return ["background-color: rgba(239, 68, 68, 0.15)"] * len(row)
            return [""] * len(row)

        styled_df = df.style.apply(highlight_rows, axis=1)
        
        st.dataframe(
            styled_df,
            use_container_width=True,
            hide_index=True,
        )
except Exception as e:
    logging.error(f"Activity log error: {e}", exc_info=True)
    st.error("Activity log panel unavailable.")

# ---------------------------------------------------------------------------
# SECTION 3 — Listings and Gaps
# ---------------------------------------------------------------------------
if not hide_panels:
    st.markdown("<br/><br/>", unsafe_allow_html=True)
    left, right = st.columns(2)

    with left:
        st.write("**🏆 Top 10 Listings**")
        try:
            top_listings = _get_listings(limit=10, min_score=0)
            if top_listings:
                rows_data = []
                for row in top_listings:
                    if row.get("fit_score") is None: continue
                    rows_data.append({
                        "Score": row["fit_score"],
                        "Title": (row.get("title") or "—")[:50],
                        "Company": (row.get("company") or "—")[:30],
                        "Reason": (row.get("fit_reason") or "—")[:60],
                    })
                if rows_data:
                    st.dataframe(
                        pd.DataFrame(rows_data),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption("No scored listings yet.")
            else:
                st.caption("No scored listings yet.")
        except Exception as e:
            logging.error(f"Listings panel error: {e}", exc_info=True)
            st.error("Listings panel unavailable.")

    with right:
        st.write("**📊 Top 10 Skill Gaps**")
        try:
            gaps = _get_latest_snapshot()
            if gaps:
                gap_rows = []
                for g in gaps[:10]:
                    gap_rows.append({
                        "Skill": g.get("skill", "?"),
                        "Blocked": g.get("listings_blocked", 0),
                        "Opp. Cost": round(g.get("opportunity_cost", 0), 1),
                        "Mean Score": round(g.get("mean_score", 0), 1),
                        "Top Score": g.get("top_score", 0),
                        "Sample": g.get("sample_size", 0),
                    })
                st.dataframe(
                    pd.DataFrame(gap_rows),
                    use_container_width=True,
                    hide_index=True,
                )
            else:
                st.caption("No gap snapshots yet.")
        except Exception as e:
            logging.error(f"Gaps panel error: {e}", exc_info=True)
            st.error("Gaps panel unavailable.")

# ---------------------------------------------------------------------------
# SECTION 4 — Footer
# ---------------------------------------------------------------------------
st.markdown("---")
col1, col2 = st.columns(2)
with col1:
    st.caption(f"Last successful cycle: {cycle_ts}")
with col2:
    st.markdown("<div style='text-align: right;'><a href='#' target='_blank'>View on GitHub</a></div>", unsafe_allow_html=True)


