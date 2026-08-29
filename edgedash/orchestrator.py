"""
orchestrator.py — state-driven cycle runner (steering rules 28-33).

What this file does:
  1. Read system state via state.read_state()
  2. Build a plan via planning.build_plan()
  3. Print the plan before executing anything          (rule 31)
  4. Execute only the active tasks, passing each task's
     goal and stop_conditions to the agent             (rule 29)
  5. Wrap each task in try/except; log failure and
     continue with remaining tasks                     (rule 32)
  6. Write exactly ONE summary row to cycle_log        (rule 33)
  7. Outcome: complete | partial | nothing_to_do

What this file does NOT do:
  - fetch, score, or analyse anything
  - decide limits (those come from planning.build_plan)
  - know anything about agents beyond their name
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

import edgedash.storage as storage
from edgedash.agents.base import AgentResult
from edgedash.config import Config
from edgedash.planning import Plan, StopConditions, Task, build_plan
from edgedash.state import read_state

_SEP = "─" * 60

# ---------------------------------------------------------------------------
# Agent registry — the ONLY place the Orchestrator couples to agent classes.
# To add a fourth agent: import it here, add one line to _REGISTRY.
# ---------------------------------------------------------------------------

def _build_registry(config: Config) -> dict[str, Any]:
    from edgedash.agents import make_fetcher, Scorer, GapAnalyzer, Verifier
    return {
        "Fetcher":     make_fetcher(config),
        "MockFetcher": make_fetcher(config),
        "Scorer":      Scorer(),
        "GapAnalyzer": GapAnalyzer(),
        "Verifier":    Verifier(),
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _print_state(state: Any) -> None:
    print(_SEP)
    print("  STATE")
    print(_SEP)
    if state.last_fetch_at:
        hrs = f"{state.hours_since_fetch:.1f}h ago" if state.hours_since_fetch is not None else ""
        print(f"  Last fetch     : {state.last_fetch_at[:19]}  ({hrs})")
    else:
        print("  Last fetch     : never")
    print(f"  Unscored       : {state.unscored_count}")
    print(f"  Gaps computed  : {state.gaps_computed_at[:19] if state.gaps_computed_at else 'never'}"
          f"  ({'stale' if state.gaps_stale else 'fresh'})")
    print(f"  Last verdict   : {state.last_cycle_verdict or 'none'}")
    print()


def _print_result(task: Task, result: AgentResult, duration: float) -> None:
    icon = "✓" if result.status in ("ok", "suspect") else "✗"
    print(
        f"  {icon}  {result.agent:<14}  "
        f"status={result.status:<8}  "
        f"new={result.records_touched}  "
        f"{duration:.1f}s"
        + (f"  |  {result.notes}" if result.notes else "")
    )


def _print_summary(
    outcome: str,
    ran: list[tuple[Task, AgentResult, float]],
    skipped: list[Task],
    cycle_start: str,
    cycle_duration: float,
) -> None:
    failed = sum(1 for _, r, _ in ran if r.status == "failed")
    total_records = sum(r.records_touched for _, r, _ in ran)

    print()
    print(_SEP)
    print("  CYCLE SUMMARY")
    print(_SEP)
    print(f"  Outcome        : {outcome}")
    print(f"  Agents run     : {len(ran)}  |  skipped: {len(skipped)}")
    print(f"  Total new rows : {total_records}")
    print(f"  Cycle duration : {cycle_duration:.1f}s")
    print(_SEP)
    print()


# ---------------------------------------------------------------------------
# Summary log row builder  (rule 33 — exactly one row per cycle)
# ---------------------------------------------------------------------------

def _build_summary_notes(
    plan: Plan,
    ran: list[tuple[Task, AgentResult, float]],
    outcome: str,
) -> str:
    parts: list[str] = [f"outcome={outcome}"]

    for task, result, duration in ran:
        parts.append(
            f"{task.agent_name}:"
            f"status={result.status},"
            f"records={result.records_touched},"
            f"duration={duration:.1f}s"
        )

    for task in plan.skipped():
        parts.append(f"{task.agent_name}:skipped,reason={task.reason}")

    return " | ".join(parts)


# ---------------------------------------------------------------------------
# Task execution helper (extracted for reuse by the retry loop)
# ---------------------------------------------------------------------------

def _execute_task(
    task: Task,
    registry: dict[str, Any],
    config: Config,
    cycle_start: str,
) -> tuple[AgentResult, float]:
    """Run a single task and return (result, duration_seconds)."""
    agent = registry.get(task.agent_name)
    if agent is None:
        return AgentResult(
            agent=task.agent_name,
            status="failed",
            records_touched=0,
            notes=f"agent '{task.agent_name}' not found in registry",
        ), 0.0

    t0 = time.monotonic()
    try:
        result = agent.run(config, config.db_path, task.stop_conditions)
    except Exception as exc:  # noqa: BLE001 — rule 32: log and continue
        result = AgentResult(
            agent=task.agent_name,
            status="failed",
            records_touched=0,
            notes=f"{type(exc).__name__}: {exc}",
        )
    duration = time.monotonic() - t0

    # Per-agent log row (kept for drill-down; summary row written below).
    storage.log_cycle(
        path=config.db_path,
        agent=result.agent,
        started_at=cycle_start,
        finished_at=_utcnow(),
        records_touched=result.records_touched,
        status=result.status,
        notes=result.notes,
    )

    return result, duration


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_cycle(config: Config) -> str:
    """Run one full cycle. Returns the outcome string for the caller.

    Outcome values:
      "complete"      — all active tasks ran successfully and verification passed
      "partial"       — at least one task failed; others continued
      "degraded"      — verification failed even after one retry (rule 36)
      "nothing_to_do" — plan was entirely skips; this is a success
    """
    cycle_start = _utcnow()
    now = datetime.now(timezone.utc)

    print()
    print("=" * 60)
    print("  EDGEDASH  —  cycle started")
    print(f"  Role  : {config.target_role}")
    print(f"  City  : {config.target_city}")
    print("=" * 60)
    print()

    cycle_t0 = time.monotonic()

    # ── 1. Initialise DB ─────────────────────────────────────────────────
    storage.init_db(config.db_path)

    # ── 2. Read state ────────────────────────────────────────────────────
    state = read_state(config, now)
    _print_state(state)

    # ── 3. Build plan ────────────────────────────────────────────────────
    plan = build_plan(state, config)

    # ── 4. Print plan (rule 31 — before execution) ───────────────────────
    print(plan.render())

    # ── 5. Nothing to do? ────────────────────────────────────────────────
    if not plan.active():
        storage.log_cycle(
            path=config.db_path,
            agent="Orchestrator",
            started_at=cycle_start,
            finished_at=_utcnow(),
            records_touched=0,
            status="nothing_to_do",
            notes=_build_summary_notes(plan, [], "nothing_to_do"),
        )
        print("  Nothing to do this cycle — all agents skipped.")
        print()
        return "nothing_to_do"

    # ── 6. Resolve agents from registry ─────────────────────────────────
    registry = _build_registry(config)

    # ── 7. Execute active tasks ──────────────────────────────────────────
    print(_SEP)
    print("  AGENT RUNS")
    print(_SEP)

    # Print skipped agents first so the full plan is visible in execution output.
    for task in plan.skipped():
        print(f"  ✗  {task.agent_name:<14}  SKIPPED           |  {task.reason}")

    ran: list[tuple[Task, AgentResult, float]] = []
    any_failed = False
    retry_count = 0
    verdict_notes = "no verification"

    for task in plan.active():
        result, duration = _execute_task(task, registry, config, cycle_start)

        if result.status == "failed":
            any_failed = True

        _print_result(task, result, duration)
        ran.append((task, result, duration))

    # ── 8. Verification & Retry (rule 36) ────────────────────────────────
    ran_agents = {t.agent_name for t, _, _ in ran}
    needs_verification = "Scorer" in ran_agents or "GapAnalyzer" in ran_agents

    if needs_verification:
        print()
        print(_SEP)
        print("  VERIFICATION")
        print(_SEP)

        v_task = Task(
            agent_name="Verifier",
            goal="verify cycle integrity",
            stop_conditions=StopConditions(),
            skipped=False,
            reason="verification",
        )
        v_res, v_dur = _execute_task(v_task, registry, config, cycle_start)
        _print_result(v_task, v_res, v_dur)
        ran.append((v_task, v_res, v_dur))
        verdict_notes = v_res.notes or ""

        if v_res.status == "failed":
            # ── Determine which agent to retry ───────────────────────────
            notes_lower = verdict_notes.lower()
            if "score_spread" in notes_lower or "extraction_sanity" in notes_lower:
                retry_agent = "Scorer"
            elif "gap_sample_size" in notes_lower:
                retry_agent = "GapAnalyzer"
            elif "freshness" in notes_lower:
                retry_agent = "Fetcher"
            else:
                retry_agent = "Scorer"  # default to Scorer

            strict = retry_agent == "Scorer"
            retry_count = 1

            print()
            print(f"  ⟳  Verification failed. Retrying {retry_agent}"
                  f"{' (strict_scoring=True)' if strict else ''} …")
            print()

            r_task = Task(
                agent_name=retry_agent,
                goal=f"retry after verification failure ({verdict_notes[:80]})",
                stop_conditions=StopConditions(
                    max_items=config.llm_batch_size,
                    max_seconds=config.score_max_seconds,
                    strict_scoring=strict,
                ),
                skipped=False,
                reason="verification_retry",
            )
            r_res, r_dur = _execute_task(r_task, registry, config, cycle_start)
            _print_result(r_task, r_res, r_dur)
            ran.append((r_task, r_res, r_dur))

            # ── Verify once more ─────────────────────────────────────────
            v2_res, v2_dur = _execute_task(v_task, registry, config, cycle_start)
            _print_result(v_task, v2_res, v2_dur)
            ran.append((v_task, v2_res, v2_dur))
            verdict_notes = v2_res.notes or ""

            if v2_res.status == "failed":
                any_failed = True
                outcome = "degraded"
                print()
                print("  ⚠  Verification failed after retry. Cycle degraded. Stopping.")
            else:
                outcome = "partial" if any_failed else "complete"
        else:
            outcome = "partial" if any_failed else "complete"
    else:
        outcome = "partial" if any_failed else "complete"

    # ── 9. One summary row (rule 33) ─────────────────────────────────────
    summary = _build_summary_notes(plan, ran, outcome)
    summary += f" | retries={retry_count} | {verdict_notes}"

    storage.log_cycle(
        path=config.db_path,
        agent="Orchestrator",
        started_at=cycle_start,
        finished_at=_utcnow(),
        records_touched=sum(r.records_touched for _, r, _ in ran),
        status=outcome,
        notes=summary,
    )

    # ── 10. Print summary ─────────────────────────────────────────────────
    _print_summary(outcome, ran, plan.skipped(), cycle_start,
                   time.monotonic() - cycle_t0)

    return outcome
