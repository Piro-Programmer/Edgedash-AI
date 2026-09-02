"""
app.py — EdgeDash Agent Activity Dashboard (read-only).

Reads through the storage module ONLY. Never writes listings, scores, or
gaps, and never runs a cycle — the scheduler (.github/workflows/cycle.yml)
owns all of that.

Per rule 38, data panels read from the LAST PASSING CYCLE only.
The activity log is the exception — it shows ALL cycles including failures.

Run:  python -m streamlit run app.py
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime

import pandas as pd
import streamlit as st

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

REPO_URL = "https://github.com/Piro-Programmer/Edgedash-AI"

# Streamlit Cloud hot-swaps app.py on a git pull but keeps already-imported
# edgedash.* modules in sys.modules, so a freshly deployed app.py can run for a
# whole process lifetime against the PREVIOUS release's storage module. Reading
# a newly added constant directly off `storage` therefore crashes the page with
# AttributeError until someone reboots the app by hand. Resolve shared
# vocabulary defensively so a stale worker degrades instead of white-screening;
# storage stays the source of truth the moment the process restarts.
_FALLBACK_FAILING = ("degraded", "partial", "failed")
FAILING_STATUSES: tuple[str, ...] = getattr(
    storage, "FAILING_STATUSES", _FALLBACK_FAILING
)

# ---------------------------------------------------------------------------
# Error reporting helper
# ---------------------------------------------------------------------------

def _panel_error(label: str, exc: Exception) -> None:
    """Show a panel-level failure without hiding what actually went wrong.

    The previous build logged the traceback server-side and rendered only
    "panel unavailable", which made every deployed failure undiagnosable.
    """
    logging.error("%s failed: %s", label, exc, exc_info=True)
    st.error(f"{label} unavailable — {type(exc).__name__}: {exc}")
    with st.expander("Show technical details"):
        st.code("".join(traceback.format_exception(exc)), language="text")


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


@st.cache_resource
def _ensure_schema(db_path: str) -> str:
    """Create any missing tables once per process.

    A freshly provisioned Postgres has no tables at all, so without this every
    query raised UndefinedTable and each panel rendered a generic error.
    init_db is CREATE TABLE IF NOT EXISTS only — it writes no rows.
    """
    storage.init_db(db_path)
    return db_path


try:
    _ensure_schema(DB)
except Exception as exc:
    logging.error("Database connection failed: %s", exc, exc_info=True)
    st.error(
        f"Cannot reach the database ({type(exc).__name__}: {exc}). "
        "Check the DATABASE_URL secret in your Streamlit Cloud app settings."
    )
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
def _count_all_listings() -> tuple[int, int]:
    """Return (total, scored).

    Goes through the storage API rather than raw SQL: the cursor wrapper
    returns mappings, so the old conn.execute(...).fetchone()[0] raised
    KeyError(0) and the surrounding except silently reported zero listings.
    """
    counts = storage.get_listing_counts(DB)
    return int(counts.get("total") or 0), int(counts.get("scored") or 0)

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
st.caption("The scheduler writes cycles. This page reads them.")

latest_verdict_status: str | None = None
latest_verdict_at: str | None = None
passing: dict | None = None
total_listings = scored_listings = 0
header_ok = True

try:
    latest_verdict_status, latest_verdict_at = _last_cycle_verdict()
    passing = _get_latest_passing_cycle()
    total_listings, scored_listings = _count_all_listings()
except Exception as exc:
    header_ok = False
    _panel_error("Header stats", exc)

# An empty database is a normal state, not a fatal one — the scheduler simply
# has not run yet. The page keeps rendering rather than calling st.stop(), and
# the "no cycles yet" verdict plus the activity log carry the explanation.
db_is_empty = header_ok and total_listings == 0 and not latest_verdict_at

st.markdown("<br/>", unsafe_allow_html=True)
col1, col2, col3, col4 = st.columns(4)

cycle_ts = "none"
if passing and passing.get("started_at"):
    cycle_ts = str(passing["started_at"])[:19].replace("T", " ")
elif latest_verdict_at:
    cycle_ts = str(latest_verdict_at)[:19].replace("T", " ")

col1.metric("Last successful cycle", cycle_ts)
col2.metric("Total listings", str(total_listings))
col3.metric("Total scored", str(scored_listings))
# "no cycles yet" reads as a state; "none" reads as a missing value.
col4.metric("Current verdict", latest_verdict_status or "no cycles yet")

# Warning banner logic
is_stale = False
hide_panels = False
if latest_verdict_status in FAILING_STATUSES:
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

EXAMPLES = (
    "Which companies posted jobs in the last 7 days?",
    "What are my top skill gaps?",
    "How many listings are scored?",
)

# The question lives in session_state so an example click and a manual edit
# drive the same widget. Passing a changing `value=` to an unkeyed text_input
# remounts it, which used to wipe whatever the user had typed.
st.session_state.setdefault("question", "")

def _use_example(text: str) -> None:
    st.session_state["question"] = text
    st.session_state["ask_now"] = True

ex_cols = st.columns(len(EXAMPLES))
for col, question in zip(ex_cols, EXAMPLES):
    col.button(question, on_click=_use_example, args=(question,), key=f"ex_{question}")

user_q = st.text_input("Question", key="question")
submit = st.button("Ask")

should_ask = submit or st.session_state.pop("ask_now", False)

if should_ask and user_q.strip():
    try:
        with st.spinner("Routing query and analyzing..."):
            from edgedash.query.ask import ask
            answer = ask(user_q)

        st.markdown(f"**Answer:** {answer.text}")

        if answer.rows:
            st.dataframe(
                pd.DataFrame(answer.rows),
                width="stretch",
                hide_index=True,
            )
        elif answer.tool_used:
            st.info("The query executed successfully but returned no rows.")

        if answer.tool_used:
            st.caption(f"Used tool: `{answer.tool_used}` with params: `{answer.params}`")
    except Exception as exc:
        _panel_error("Ask panel", exc)
elif should_ask:
    st.warning("Type a question first.")

# ---------------------------------------------------------------------------
# SECTION 2 — Agent Activity Log
# ---------------------------------------------------------------------------
st.markdown("<br/>", unsafe_allow_html=True)
st.subheader("Agent activity log")
st.caption("Most recent 30 cycles, including failed and degraded runs.")

try:
    cycles = _get_recent_cycles(30)

    def parse_cycle_row(row: dict) -> dict:
        started = row.get("started_at") or ""
        finished = row.get("finished_at") or ""
        ts = started[:19].replace("T", " ") + " UTC" if started else "—"

        duration = "—"
        if started and finished:
            try:
                t1 = datetime.fromisoformat(started)
                t2 = datetime.fromisoformat(finished)
                dur_sec = (t2 - t1).total_seconds()
                duration = f"{dur_sec:.1f}s"
            except ValueError:
                pass

        notes = row.get("notes") or ""
        agents_run: list[str] = []
        skipped: list[str] = []
        failed_check = "—"
        retries = "—"

        for part in notes.split("|"):
            part = part.strip()
            if "status=" in part:
                agent = part.split(":")[0].strip()
                # The Verifier runs twice on a retry cycle; list it once.
                if agent not in ("cycle", "Orchestrator") and agent not in agents_run:
                    agents_run.append(agent)
            elif "skipped" in part:
                agent = part.split(":")[0].strip()
                if agent not in ("cycle", "Orchestrator") and agent not in skipped:
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
        st.caption("No cycle data yet.")
        if db_is_empty:
            # Kept small and in place of the table rather than as a banner at
            # the top — the header already states there are no cycles.
            st.caption(
                "Run the **EdgeDash cycle** workflow in GitHub Actions to "
                "populate this database."
            )
    else:
        df = pd.DataFrame([parse_cycle_row(r) for r in cycles])

        def highlight_rows(row):
            # Apply dark red background to failed / degraded rows
            if row["Verdict"] in FAILING_STATUSES:
                return ["background-color: rgba(239, 68, 68, 0.15)"] * len(row)
            return [""] * len(row)

        st.dataframe(
            df.style.apply(highlight_rows, axis=1),
            width="stretch",
            hide_index=True,
        )
except Exception as exc:
    _panel_error("Activity log", exc)

# ---------------------------------------------------------------------------
# SECTION 3 — Listings and Gaps
# ---------------------------------------------------------------------------
if not hide_panels:
    st.markdown("<br/><br/>", unsafe_allow_html=True)
    left, right = st.columns(2)

    with left:
        st.write("**🏆 Top 10 Listings**")
        st.caption(f"Listings scoring ≥ {cfg.min_fit_score} (min_fit_score in config.yaml).")
        try:
            # Honour the configured threshold — the previous build hardcoded 0,
            # so min_fit_score in config.yaml had no effect on the dashboard.
            top_listings = _get_listings(limit=10, min_score=cfg.min_fit_score)
            rows_data = [
                {
                    "Score": row["fit_score"],
                    "Title": (row.get("title") or "—")[:50],
                    "Company": (row.get("company") or "—")[:30],
                    "Reason": (row.get("fit_reason") or "—")[:60],
                }
                for row in top_listings
                if row.get("fit_score") is not None
            ]
            if rows_data:
                st.dataframe(
                    pd.DataFrame(rows_data),
                    width="stretch",
                    hide_index=True,
                )
            elif scored_listings:
                st.caption(
                    f"{scored_listings} listing(s) scored, but none reached "
                    f"the min_fit_score of {cfg.min_fit_score}."
                )
            else:
                st.caption("No scored listings yet.")
        except Exception as exc:
            _panel_error("Listings panel", exc)

    with right:
        st.write("**📊 Top 10 Skill Gaps**")
        try:
            gaps = _get_latest_snapshot()
            if gaps:
                gap_rows = [
                    {
                        "Skill": g.get("skill", "?"),
                        "Blocked": g.get("listings_blocked", 0),
                        "Opp. Cost": round(g.get("opportunity_cost") or 0, 1),
                        "Mean Score": round(g.get("mean_score") or 0, 1),
                        "Top Score": g.get("top_score", 0),
                        "Sample": g.get("sample_size", 0),
                    }
                    for g in gaps[:10]
                ]
                st.dataframe(
                    pd.DataFrame(gap_rows),
                    width="stretch",
                    hide_index=True,
                )
            else:
                st.caption("No gap snapshots yet.")
        except Exception as exc:
            _panel_error("Gaps panel", exc)

# ---------------------------------------------------------------------------
# SECTION 4 — Footer
# ---------------------------------------------------------------------------
st.markdown("---")
col1, col2 = st.columns(2)
with col1:
    st.caption(f"Last successful cycle: {cycle_ts}")
with col2:
    st.markdown(
        f"<div style='text-align: right;'>"
        f"<a href='{REPO_URL}' target='_blank' rel='noopener'>View on GitHub</a>"
        f"</div>",
        unsafe_allow_html=True,
    )
