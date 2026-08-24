"""
orchestrator.py — reads state, decides what to run, delegates to agents,
logs every outcome, and prints a readable cycle summary.

The Orchestrator never fetches data or scores listings directly.
It only reads state and calls agents.
"""

from __future__ import annotations

from datetime import datetime, timezone

import edgedash.storage as storage
from edgedash.agents import make_fetcher, Scorer, GapAnalyzer
from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config


def _build_pipeline(config: Config) -> list[Agent]:
    """Construct the ordered agent pipeline for this cycle."""
    return [
        make_fetcher(config),
        Scorer(),
        GapAnalyzer(),
    ]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEP = "─" * 60


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_state(last_fetch: str | None, unscored: int) -> None:
    print(_SEP)
    print("  STATE")
    print(_SEP)
    if last_fetch:
        print(f"  Last fetch   : {last_fetch}")
    else:
        print("  Last fetch   : never (fresh database)")
    print(f"  Unscored     : {unscored} listing(s)")
    print()


def _print_plan(decisions: list[tuple[Agent, str]]) -> None:
    print(_SEP)
    print("  PLAN")
    print(_SEP)
    for agent, reason in decisions:
        print(f"  {agent.name:<20} → {reason}")
    print()


def _print_result(result: AgentResult) -> None:
    icon = "✓" if result.status == "ok" else "✗"
    status_label = result.status.upper()
    print(f"  {icon}  {result.agent:<20} [{status_label}]  "
          f"records touched: {result.records_touched}")
    if result.notes:
        print(f"     {result.notes}")


def _print_summary(results: list[AgentResult], cycle_start: str) -> None:
    finished = _utcnow()
    ok_count = sum(1 for r in results if r.status == "ok")
    fail_count = len(results) - ok_count
    total_records = sum(r.records_touched for r in results)

    print()
    print(_SEP)
    print("  CYCLE SUMMARY")
    print(_SEP)
    print(f"  Started      : {cycle_start}")
    print(f"  Finished     : {finished}")
    print(f"  Agents run   : {len(results)}  "
          f"({ok_count} ok, {fail_count} failed)")
    print(f"  Total records: {total_records}")
    print(_SEP)
    print()


# ---------------------------------------------------------------------------
# Planner — decides which agents to run and states the reason
# ---------------------------------------------------------------------------

def _plan(
    pipeline: list[Agent],
    last_fetch: str | None,
    unscored: int,
) -> list[tuple[Agent, str]]:
    decisions: list[tuple[Agent, str]] = []

    for agent in pipeline:
        if agent.name in ("MockFetcher", "Fetcher"):
            decisions.append((agent, "always fetch on every cycle"))

        elif agent.name == "Scorer":
            if unscored > 0:
                decisions.append(
                    (agent, f"{unscored} unscored listing(s) waiting"))
            else:
                decisions.append((agent, "no unscored listings — will skip"))

        elif agent.name == "GapAnalyzer":
            decisions.append((agent, "runs after Scorer on every cycle"))

        else:
            decisions.append((agent, "registered agent"))

    return decisions


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_cycle(config: Config) -> None:
    """Initialise the db, read state, plan, run agents, log, and summarise."""
    cycle_start = _utcnow()

    print()
    print("=" * 60)
    print("  EDGEDASH  —  cycle started")
    print(f"  Role  : {config.target_role}")
    print(f"  City  : {config.target_city}")
    print("=" * 60)
    print()

    # ── 1. Initialise DB ────────────────────────────────────────────────────
    storage.init_db(config.db_path)

    # ── 2. Read state ───────────────────────────────────────────────────────
    last_fetch = storage.last_fetch_time(config.db_path)
    unscored = storage.count_unscored(config.db_path)
    _print_state(last_fetch, unscored)

    # ── 3. Plan ─────────────────────────────────────────────────────────────
    pipeline = _build_pipeline(config)
    decisions = _plan(pipeline, last_fetch, unscored)
    _print_plan(decisions)

    # ── 4. Run agents ───────────────────────────────────────────────────────
    print(_SEP)
    print("  AGENT RUNS")
    print(_SEP)

    results: list[AgentResult] = []
    for agent, _reason in decisions:
        agent_start = _utcnow()
        try:
            result = agent.run(config, config.db_path)
        except Exception as exc:
            result = AgentResult(
                agent=agent.name,
                status="failed",
                records_touched=0,
                notes=f"{type(exc).__name__}: {exc}",
            )
        agent_finish = _utcnow()

        _print_result(result)

        # ── 5. Log every run ─────────────────────────────────────────────────
        storage.log_cycle(
            path=config.db_path,
            agent=result.agent,
            started_at=agent_start,
            finished_at=agent_finish,
            records_touched=result.records_touched,
            status=result.status,
            notes=result.notes,
        )
        results.append(result)

    # ── 6. Summary ──────────────────────────────────────────────────────────
    _print_summary(results, cycle_start)
