"""
gaps.py — morning read for skill gaps.

Usage
-----
    python -m edgedash.gaps            # latest snapshot table
    python -m edgedash.gaps --trend    # trend vs earliest snapshot
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOP_N = 10


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _bar(value: float, max_value: float, width: int = 20) -> str:
    if max_value <= 0:
        return " " * width
    filled = int(round(value / max_value * width))
    return "█" * max(0, min(width, filled)) + "░" * (width - max(0, min(width, filled)))


def _confidence_tag(row: dict) -> str:
    return " ⚠ low confidence" if row.get("low_confidence") else ""


def _fmt_date(iso: str) -> str:
    """Trim an ISO timestamp to 'YYYY-MM-DD HH:MM'."""
    return iso[:16].replace("T", " ")


# ---------------------------------------------------------------------------
# Latest-snapshot report  (default mode)
# ---------------------------------------------------------------------------

def print_report(rows: list[dict]) -> None:
    if not rows:
        print("No gap snapshot found. Run the full cycle first.")
        return

    computed_at = rows[0].get("computed_at", "unknown")
    total_exposures = sum(r["listings_blocked"] for r in rows)

    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  EDGEDASH — SKILL GAP REPORT                                     ║")
    print(f"║  Snapshot : {_fmt_date(computed_at)}  "
          f"(sample: {total_exposures} gap-exposures)".ljust(67) + "║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()

    skill_w = max(max(len(r["skill"]) for r in rows), 18)
    bar_max = max(r["opportunity_cost"] for r in rows)

    header = (
        f"  {'#':>3}  {'skill':<{skill_w}}  "
        f"{'blocked':>7}  {'cost':>6}  {'mean':>5}  {'top':>4}  {'n+':>4}  opportunity"
    )
    print(header)
    print("  " + "─" * (len(header) - 2 + 20))

    for i, row in enumerate(rows, start=1):
        print(
            f"  {i:>3}  {row['skill']:<{skill_w}}  "
            f"{row['listings_blocked']:>7}  "
            f"{row['opportunity_cost']:>6.1f}  "
            f"{row['mean_score']:>5.1f}  "
            f"{row['top_score']:>4}  "
            f"{row['also_nice_to_have']:>4}  "
            f"{_bar(row['opportunity_cost'], bar_max)}"
            f"{_confidence_tag(row)}"
        )

    print()
    print(
        "  blocked = listings requiring this skill you lack\n"
        "  cost    = sum(score/100) — weighted by listing quality  [ranking key]\n"
        "  mean    = mean fit score of blocked listings\n"
        "  top     = highest fit score blocked by this gap\n"
        "  n+      = also appeared as nice-to-have (tracked separately)\n"
        "  ⚠       = fewer than 3 listings — treat as low confidence\n"
    )


# ---------------------------------------------------------------------------
# Trend report  (--trend mode)
# ---------------------------------------------------------------------------

def _trend_arrow(change: float) -> str:
    if change > 0.05:
        return "↑"
    if change < -0.05:
        return "↓"
    return "→"


def print_trend(
    earliest_rows: list[dict],
    latest_rows: list[dict],
    earliest_date: str,
    latest_date: str,
) -> None:
    # Index earliest snapshot by skill for O(1) lookup.
    earliest: dict[str, dict] = {r["skill"]: r for r in earliest_rows}
    latest_skills: list[str] = [r["skill"] for r in latest_rows[:_TOP_N]]
    earliest_top10: set[str] = {r["skill"] for r in earliest_rows[:_TOP_N]}

    skill_w = max(
        max((len(s) for s in latest_skills), default=18),
        18,
    )

    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  EDGEDASH — SKILL GAP TREND                                      ║")
    print(f"║  From : {_fmt_date(earliest_date)}".ljust(68) + "║")
    print(f"║  To   : {_fmt_date(latest_date)}".ljust(68) + "║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()

    header = (
        f"  {'#':>3}  {'skill':<{skill_w}}  "
        f"{'earliest':>8}  {'latest':>8}  {'Δ':>7}  {'Δ%':>6}  {'':2}"
    )
    print(header)
    print("  " + "─" * (len(header) + 4))

    for i, row in enumerate(latest_rows[:_TOP_N], start=1):
        skill = row["skill"]
        latest_cost = row["opportunity_cost"]

        if skill not in earliest:
            # Skill wasn't tracked at all in the earliest snapshot.
            tag = "NEW"
            print(
                f"  {i:>3}  {skill:<{skill_w}}  "
                f"{'—':>8}  {latest_cost:>8.2f}  {'—':>7}  {'—':>6}  {tag}"
            )
        else:
            prev_cost = earliest[skill]["opportunity_cost"]
            delta = latest_cost - prev_cost
            pct = (delta / prev_cost * 100) if prev_cost != 0 else 0.0
            arrow = _trend_arrow(delta)
            tag = f"{arrow} {'+' if delta >= 0 else ''}{delta:+.2f}"
            print(
                f"  {i:>3}  {skill:<{skill_w}}  "
                f"{prev_cost:>8.2f}  {latest_cost:>8.2f}  "
                f"{delta:>+7.2f}  {pct:>+5.1f}%  {arrow}"
            )

    # Skills that were in the earliest top-10 but dropped out of current top-10.
    current_top_skills = set(latest_skills)
    dropped = [s for s in earliest_top10 if s not in current_top_skills]
    if dropped:
        print()
        print("  DROPPED OUT of top 10 since earliest snapshot:")
        for skill in dropped:
            prev = earliest[skill]["opportunity_cost"]
            print(f"    {skill}  (was cost {prev:.2f})")

    print()
    print(
        "  Δ      = change in opportunity_cost (absolute)\n"
        "  Δ%     = percentage change\n"
        "  ↑ / ↓  = cost moved by more than 0.05\n"
        "  →      = roughly stable (change ≤ 0.05)\n"
        "  NEW    = skill not present in the earliest snapshot\n"
    )


def print_trend_single_snapshot(computed_at: str) -> None:
    """Printed when only one snapshot exists — no fabrication."""
    print()
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  EDGEDASH — SKILL GAP TREND                                      ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()
    print(f"  Only one snapshot exists (taken {_fmt_date(computed_at)}).")
    print()
    print("  A trend requires at least two snapshots from different runs.")
    print("  Run the full cycle again — each run writes a new snapshot.")
    print()
    print("  Tip: run daily and you'll have a meaningful trend within 3 days.")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

    from edgedash.config import load_config
    import edgedash.storage as storage

    cfg = load_config()
    storage.init_db(cfg.db_path)
    trend_mode = "--trend" in sys.argv

    if not trend_mode:
        rows = storage.get_latest_snapshot(cfg.db_path)
        print_report(rows)
        sys.exit(0)

    # ── trend mode ────────────────────────────────────────────────────────
    runs = storage.get_snapshot_run_ids(cfg.db_path)

    if not runs:
        print("No snapshots found. Run the full cycle first.")
        sys.exit(0)

    if len(runs) == 1:
        _, computed_at = runs[0]
        print_trend_single_snapshot(computed_at)
        sys.exit(0)

    earliest_run_id, earliest_date = runs[0]
    latest_run_id,   latest_date   = runs[-1]

    earliest_rows = storage.get_snapshot_by_run_id(cfg.db_path, earliest_run_id)
    latest_rows   = storage.get_snapshot_by_run_id(cfg.db_path, latest_run_id)

    print_trend(earliest_rows, latest_rows, earliest_date, latest_date)
