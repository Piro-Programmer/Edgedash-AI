"""
agents/__init__.py — agent registry and factory.

The only place that decides which concrete fetcher class is used.
Set `use_mock_fetcher: true` in config.yaml to go offline without
touching any other file.
"""

from __future__ import annotations

from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.fetcher import Fetcher
from edgedash.agents.gap_analyzer import GapAnalyzer
from edgedash.agents.mock_fetcher import MockFetcher
from edgedash.agents.scorer import Scorer
from edgedash.config import Config


def make_fetcher(config: Config) -> Agent:
    """Return a MockFetcher when offline mode is requested, else the real Fetcher."""
    if config.use_mock_fetcher:
        return MockFetcher()
    return Fetcher()


__all__ = [
    "Agent", "AgentResult",
    "Fetcher", "MockFetcher", "Scorer", "GapAnalyzer",
    "make_fetcher",
]
