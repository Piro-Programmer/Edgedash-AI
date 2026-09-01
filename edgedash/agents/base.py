"""
base.py — shared Agent protocol and AgentResult dataclass.

Every agent in EdgeDash must satisfy the Agent protocol:
  - a `name` attribute identifying the agent in logs
  - a `run(config, storage_path, stop_conditions)` method returning an AgentResult

stop_conditions is optional at the call site (defaults to None) so existing
agents that have not yet been updated continue to work without changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from edgedash.config import Config
from edgedash.planning import StopConditions


@dataclass
class AgentResult:
    agent: str
    status: str          # "ok" | "failed" | "suspect"
    records_touched: int
    notes: str | None = None


@runtime_checkable
class Agent(Protocol):
    name: str

    def run(
        self,
        config: Config,
        storage_path: str,
        stop_conditions: StopConditions | None = None,
    ) -> AgentResult:
        ...

