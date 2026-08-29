"""
planning.py — state-driven cycle planning (steering rules 28-31).

No I/O. No LLM. Pure function of (SystemState, Config).

Public API
----------
    build_plan(state, config) -> Plan

A Plan is an ordered list of Tasks. Every agent appears in the list —
skipped agents are explicitly present with skipped=True and a reason,
never silently absent (rule 31).
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class StopConditions:
    """Limits the Orchestrator passes down to an agent (rule 29)."""
    max_items: int | None = None
    max_seconds: int | None = None
    max_pages: int | None = None
    strict_scoring: bool = False   # retry flag: widen score distribution

    def render(self) -> str:
        parts = []
        if self.max_items is not None:
            parts.append(f"max_items={self.max_items}")
        if self.max_pages is not None:
            parts.append(f"max_pages={self.max_pages}")
        if self.max_seconds is not None:
            parts.append(f"max_seconds={self.max_seconds}")
        if self.strict_scoring:
            parts.append("strict_scoring=True")
        return ", ".join(parts) if parts else "—"


@dataclass(frozen=True)
class Task:
    agent_name: str
    goal: str
    stop_conditions: StopConditions
    skipped: bool
    reason: str             # names the state value that caused the decision


@dataclass
class Plan:
    tasks: list[Task] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def active(self) -> list[Task]:
        return [t for t in self.tasks if not t.skipped]

    def skipped(self) -> list[Task]:
        return [t for t in self.tasks if t.skipped]

    # ------------------------------------------------------------------
    # Rendering  (rule 31 — plan printed before execution)
    # ------------------------------------------------------------------

    def render(self) -> str:
        sep = "─" * 60
        lines: list[str] = [sep, "  PLAN", sep]
        for task in self.tasks:
            status = "SKIP" if task.skipped else "RUN "
            lines.append(f"  {task.agent_name:<12} {status}  {task.goal}")
            lines.append(f"               stop: {task.stop_conditions.render()}")
            lines.append(f"               why : {task.reason}")
            lines.append(sep)
        active_n  = len(self.active())
        skipped_n = len(self.skipped())
        lines.append(
            f"  {active_n} agent(s) will run  ·  {skipped_n} skipped"
        )
        lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public: build_plan
# ---------------------------------------------------------------------------

def build_plan(state: "SystemState", config: "Config") -> Plan:  # type: ignore[name-defined]
    """Return an explicit Plan for the current SystemState.

    Decision rules (all thresholds from config — never hardcoded):
      fetch   : hours_since_fetch >= fetch_interval_hours, or never fetched
      score   : unscored_count > 0
      analyse : gaps_stale is True, or gaps have never been computed

    Every agent appears in the returned Plan. Skipped agents carry a reason
    naming the state value that drove the decision (rule 31).
    """
    # Import here to avoid a circular import at module level; both modules
    # are thin data-only imports so the cost is negligible.
    from edgedash.config import Config
    from edgedash.state import SystemState

    tasks: list[Task] = []

    # ── Fetcher ───────────────────────────────────────────────────────────
    if (
        state.hours_since_fetch is None
        or state.hours_since_fetch >= config.fetch_interval_hours
    ):
        if state.hours_since_fetch is None:
            fetch_reason = "hours_since_fetch=None (never fetched)"
        else:
            fetch_reason = (
                f"hours_since_fetch={state.hours_since_fetch:.1f}"
                f" >= {config.fetch_interval_hours}"
            )
        tasks.append(Task(
            agent_name="Fetcher",
            goal="fetch new listings from all enabled sources",
            stop_conditions=StopConditions(
                max_pages=config.fetch_max_pages,
                max_items=config.fetch_max_listings,
            ),
            skipped=False,
            reason=fetch_reason,
        ))
    else:
        tasks.append(Task(
            agent_name="Fetcher",
            goal="fetch new listings",
            stop_conditions=StopConditions(),
            skipped=True,
            reason=(
                f"skipped: hours_since_fetch={state.hours_since_fetch:.1f}"
                f" < {config.fetch_interval_hours}"
            ),
        ))

    # ── Scorer ────────────────────────────────────────────────────────────
    if state.unscored_count > 0:
        tasks.append(Task(
            agent_name="Scorer",
            goal=f"score up to {config.llm_batch_size} unscored listings",
            stop_conditions=StopConditions(
                max_items=config.llm_batch_size,
                max_seconds=config.score_max_seconds,
            ),
            skipped=False,
            reason=f"unscored_count={state.unscored_count}",
        ))
    else:
        tasks.append(Task(
            agent_name="Scorer",
            goal="score unscored listings",
            stop_conditions=StopConditions(),
            skipped=True,
            reason="skipped: unscored_count=0",
        ))

    # ── GapAnalyzer ───────────────────────────────────────────────────────
    if state.gaps_computed_at is None or state.gaps_stale:
        if state.gaps_computed_at is None:
            gap_reason = "gaps_computed_at=None (never run)"
        else:
            gap_reason = "gaps_stale=True (scores newer than last snapshot)"
        tasks.append(Task(
            agent_name="GapAnalyzer",
            goal="compute skill gap snapshot from all scored listings",
            stop_conditions=StopConditions(
                max_seconds=config.analyse_max_seconds,
            ),
            skipped=False,
            reason=gap_reason,
        ))
    else:
        tasks.append(Task(
            agent_name="GapAnalyzer",
            goal="compute skill gaps",
            stop_conditions=StopConditions(),
            skipped=True,
            reason=f"skipped: gaps_stale=False, gaps_computed_at={state.gaps_computed_at}",
        ))

    return Plan(tasks=tasks)
