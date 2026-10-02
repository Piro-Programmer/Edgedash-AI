"""
views/tracked.py — the signed-in user's job tracker (saved → offer).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import edgedash.storage as storage
from edgedash.ui import bootstrap, panel_error, require_user

STATUSES = storage.TRACKED_STATUSES
STAGE_ICONS = {"saved": "📌", "applied": "📨", "interview": "🗣️", "offer": "🎉", "rejected": "✖️"}

cfg = bootstrap()
user = require_user(cfg.db_path)

st.title("Tracked Jobs")

try:
    rows = storage.list_tracked(cfg.db_path, user["id"])
except Exception as exc:
    panel_error("Tracked jobs", exc)
    st.stop()

if not rows:
    st.info("You're not tracking any jobs yet. Select rows in **My Dashboard → Top matches** "
            "and press **Track** to add them here.")
    st.stop()

# ── Pipeline summary ─────────────────────────────────────────────────────────
counts = pd.Series([r["status"] for r in rows]).value_counts()
for col, status in zip(st.columns(len(STATUSES)), STATUSES):
    col.metric(f"{STAGE_ICONS[status]} {status.title()}", int(counts.get(status, 0)))

stage_filter = st.pills("Show", STATUSES, selection_mode="multi", default=list(STATUSES),
                        format_func=lambda s: f"{STAGE_ICONS[s]} {s.title()}")

df = pd.DataFrame([
    {
        "listing_id": r["listing_id"],
        "Status": r["status"],
        "Title": r.get("title") or "(listing no longer available)",
        "Company": r.get("company") or "—",
        "Location": r.get("location") or "—",
        "Link": r.get("url"),
        "Notes": r.get("notes") or "",
        "Updated": pd.to_datetime(r.get("updated_at"), utc=True, errors="coerce"),
        "Remove": False,
    }
    for r in rows
])
df = df[df["Status"].isin(stage_filter or [])].reset_index(drop=True)

if df.empty:
    st.caption("No jobs in the selected stages.")
    st.stop()

st.caption("Edit a status or note directly in the table, tick **Remove** to stop tracking, then save.")
edited = st.data_editor(
    df,
    hide_index=True,
    width="stretch",
    # Versioned key: after a save the editor must start fresh, otherwise its
    # stored row edits get re-applied to the reloaded (shorter) table.
    key=f"tracked_editor_{st.session_state.get('_tracked_version', 0)}",
    disabled=["Title", "Company", "Location", "Link", "Updated"],
    column_order=["Status", "Title", "Company", "Location", "Link", "Notes", "Updated", "Remove"],
    column_config={
        "Status": st.column_config.SelectboxColumn("Status", options=list(STATUSES), required=True),
        "Link": st.column_config.LinkColumn("Link", display_text="Open ↗"),
        "Notes": st.column_config.TextColumn("Notes", max_chars=500, width="large"),
        "Updated": st.column_config.DatetimeColumn("Updated", format="MMM D, HH:mm"),
        "Remove": st.column_config.CheckboxColumn("Remove"),
    },
)

changed = edited[
    (edited["Status"] != df["Status"]) | (edited["Notes"] != df["Notes"]) | edited["Remove"]
]

if st.button(f"Save {len(changed)} change(s)", type="primary", disabled=changed.empty):
    try:
        for _, row in changed.iterrows():
            if row["Remove"]:
                storage.remove_tracked(cfg.db_path, user["id"], row["listing_id"])
            else:
                storage.set_tracked_status(cfg.db_path, user["id"], row["listing_id"],
                                           row["Status"], row["Notes"] or None)
    except Exception as exc:
        panel_error("Saving changes", exc)
    else:
        st.session_state["_tracked_version"] = st.session_state.get("_tracked_version", 0) + 1
        st.toast("Tracker updated.", icon="✅")
        st.rerun()
