"""
tests/test_planning.py — unit tests for build_plan().

build_plan() is a pure function of (SystemState, Config).
No I/O, no network, no database, no mocks needed.

Four cases required by the spec:
  1. Everything stale      — all three agents RUN
  2. Nothing to do         — all three agents SKIP
  3. Only unscored         — Fetcher skips, Scorer runs, GapAnalyzer runs
  4. Gaps stale but no     — Fetcher skips, Scorer skips, GapAnalyzer runs
     unscored listings
"""

from __future__ import annotations

import pytest
from edgedash.planning import build_plan, Plan, Task
from edgedash.state import SystemState
from edgedash.config import Config
from dataclasses import replace


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_config(**overrides) -> Config:
    """Return a minimal Config suitable for planning tests."""
    base = Config(
        target_role="Engineer",
        target_city="Berlin",
        keywords=[],
        my_skills=[],
        experience_years=0,
        db_path=":memory:",
        min_fit_score=50,
        fetch_interval_hours=6.0,
        fetch_max_pages=5,
        fetch_max_listings=200,
        score_max_seconds=300,
        analyse_max_seconds=60,
        llm_batch_size=25,
    )
    # dataclasses are frozen so we use replace() for overrides
    return replace(base, **overrides)


def _make_state(**overrides) -> SystemState:
    """Return a fully-populated SystemState; individual fields can be overridden."""
    base = SystemState(
        last_fetch_at="2026-08-09T06:00:00+00:00",
        hours_since_fetch=1.0,          # fresh — within fetch_interval_hours
        unscored_count=0,
        gaps_computed_at="2026-08-09T07:00:00+00:00",
        gaps_stale=False,
        last_cycle_verdict="ok",
        last_cycle_at="2026-08-09T07:00:00+00:00",
    )
    return replace(base, **overrides)


def _agent(plan: Plan, name: str) -> Task:
    """Look up a task by agent name; fail clearly if absent."""
    for t in plan.tasks:
        if t.agent_name == name:
            return t
    raise KeyError(f"No task for agent '{name}' in plan")


# ---------------------------------------------------------------------------
# Case 1 — everything stale: all three agents RUN
# ---------------------------------------------------------------------------

class TestEverythingStale:
    """Never fetched, lots unscored, gaps never computed."""

    def setup_method(self):
        cfg = _make_config()
        state = _make_state(
            last_fetch_at=None,
            hours_since_fetch=None,      # never fetched
            unscored_count=120,
            gaps_computed_at=None,       # never run
            gaps_stale=True,
        )
        self.plan = build_plan(state, cfg)
        self.cfg  = cfg

    def test_all_three_run(self):
        assert len(self.plan.active()) == 3

    def test_none_skipped(self):
        assert len(self.plan.skipped()) == 0

    def test_fetcher_runs_with_reason(self):
        t = _agent(self.plan, "Fetcher")
        assert not t.skipped
        assert "None" in t.reason or "never" in t.reason.lower()

    def test_fetcher_stop_conditions(self):
        t = _agent(self.plan, "Fetcher")
        assert t.stop_conditions.max_pages   == self.cfg.fetch_max_pages
        assert t.stop_conditions.max_items   == self.cfg.fetch_max_listings

    def test_scorer_runs_with_count(self):
        t = _agent(self.plan, "Scorer")
        assert not t.skipped
        assert "120" in t.reason

    def test_scorer_stop_conditions(self):
        t = _agent(self.plan, "Scorer")
        assert t.stop_conditions.max_items   == self.cfg.llm_batch_size
        assert t.stop_conditions.max_seconds == self.cfg.score_max_seconds

    def test_gap_analyzer_runs_never_computed(self):
        t = _agent(self.plan, "GapAnalyzer")
        assert not t.skipped
        assert "None" in t.reason or "never" in t.reason.lower()

    def test_gap_analyzer_stop_conditions(self):
        t = _agent(self.plan, "GapAnalyzer")
        assert t.stop_conditions.max_seconds == self.cfg.analyse_max_seconds


# ---------------------------------------------------------------------------
# Case 2 — nothing to do: all three agents SKIP
# ---------------------------------------------------------------------------

class TestNothingToDo:
    """Recently fetched, nothing unscored, gaps fresh."""

    def setup_method(self):
        cfg = _make_config(fetch_interval_hours=6.0)
        state = _make_state(
            hours_since_fetch=2.0,    # 2 h < 6 h threshold → skip fetch
            unscored_count=0,
            gaps_stale=False,
            gaps_computed_at="2026-08-09T07:00:00+00:00",
        )
        self.plan = build_plan(state, cfg)

    def test_none_run(self):
        assert len(self.plan.active()) == 0

    def test_all_three_skipped(self):
        assert len(self.plan.skipped()) == 3

    def test_fetcher_skip_reason_names_state(self):
        t = _agent(self.plan, "Fetcher")
        assert t.skipped
        assert "hours_since_fetch" in t.reason
        assert "2.0" in t.reason

    def test_scorer_skip_reason_names_state(self):
        t = _agent(self.plan, "Scorer")
        assert t.skipped
        assert "unscored_count=0" in t.reason

    def test_gap_analyzer_skip_reason_names_state(self):
        t = _agent(self.plan, "GapAnalyzer")
        assert t.skipped
        assert "gaps_stale=False" in t.reason

    def test_render_contains_skip(self):
        rendered = self.plan.render()
        assert "SKIP" in rendered
        assert "0 agent(s) will run" in rendered
        assert "3 skipped" in rendered


# ---------------------------------------------------------------------------
# Case 3 — only unscored listings: Fetcher skips, Scorer + GapAnalyzer run
# ---------------------------------------------------------------------------

class TestOnlyUnscored:
    """Recent fetch, unscored backlog, gaps stale because new scores exist."""

    def setup_method(self):
        cfg = _make_config(fetch_interval_hours=6.0)
        state = _make_state(
            hours_since_fetch=1.5,    # fresh
            unscored_count=41,
            gaps_stale=True,          # new scores arrived since last snapshot
            gaps_computed_at="2026-08-09T05:00:00+00:00",
        )
        self.plan = build_plan(state, cfg)

    def test_fetcher_skipped(self):
        assert _agent(self.plan, "Fetcher").skipped

    def test_scorer_runs(self):
        assert not _agent(self.plan, "Scorer").skipped

    def test_scorer_reason_names_count(self):
        assert "41" in _agent(self.plan, "Scorer").reason

    def test_gap_analyzer_runs(self):
        assert not _agent(self.plan, "GapAnalyzer").skipped

    def test_gap_analyzer_reason_names_stale(self):
        assert "stale" in _agent(self.plan, "GapAnalyzer").reason.lower()

    def test_two_agents_active(self):
        assert len(self.plan.active()) == 2


# ---------------------------------------------------------------------------
# Case 4 — gaps stale but nothing unscored: Fetcher + Scorer skip, GapAnalyzer runs
# ---------------------------------------------------------------------------

class TestGapsStaleNoUnscored:
    """All listings scored, gap snapshot out of date."""

    def setup_method(self):
        cfg = _make_config(fetch_interval_hours=6.0)
        state = _make_state(
            hours_since_fetch=3.0,    # fresh
            unscored_count=0,
            gaps_stale=True,
            gaps_computed_at="2026-08-08T20:00:00+00:00",
        )
        self.plan = build_plan(state, cfg)

    def test_fetcher_skipped(self):
        assert _agent(self.plan, "Fetcher").skipped

    def test_scorer_skipped(self):
        assert _agent(self.plan, "Scorer").skipped

    def test_gap_analyzer_runs(self):
        assert not _agent(self.plan, "GapAnalyzer").skipped

    def test_only_one_active(self):
        assert len(self.plan.active()) == 1
        assert self.plan.active()[0].agent_name == "GapAnalyzer"

    def test_plan_order_preserved(self):
        names = [t.agent_name for t in self.plan.tasks]
        assert names == ["Fetcher", "Scorer", "GapAnalyzer"]


# ---------------------------------------------------------------------------
# Render tests — spot-checks across all cases
# ---------------------------------------------------------------------------

class TestRender:
    def test_render_shows_all_agents(self):
        cfg = _make_config()
        state = _make_state(hours_since_fetch=None, unscored_count=5,
                            gaps_computed_at=None, gaps_stale=True)
        rendered = build_plan(state, cfg).render()
        assert "Fetcher" in rendered
        assert "Scorer" in rendered
        assert "GapAnalyzer" in rendered

    def test_render_shows_stop_conditions(self):
        cfg = _make_config(fetch_max_pages=3, llm_batch_size=10)
        state = _make_state(hours_since_fetch=None, unscored_count=10,
                            gaps_computed_at=None, gaps_stale=True)
        rendered = build_plan(state, cfg).render()
        assert "max_pages=3" in rendered
        assert "max_items=10" in rendered

    def test_render_shows_run_count(self):
        cfg = _make_config()
        state = _make_state(hours_since_fetch=0.5, unscored_count=0,
                            gaps_stale=False,
                            gaps_computed_at="2026-08-09T07:00:00+00:00")
        rendered = build_plan(state, cfg).render()
        assert "0 agent(s) will run" in rendered
        assert "3 skipped" in rendered
