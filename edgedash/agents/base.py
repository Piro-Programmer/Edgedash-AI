"""
base.py — shared Agent protocol and AgentResult dataclass.

Every agent in EdgeDash must satisfy the Agent protocol:
  - a `name` attribute identifying the agent in logs
  - a `run(config, storage_path)` method returning an AgentResult
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from edgedash.config import Config


@dataclass
class AgentResult:
    agent: str
    status: str          # "ok" | "failed"
    records_touched: int
    notes: str | None = None


@runtime_checkable
class Agent(Protocol):
    name: str

    def run(self, config: Config, storage_path: str) -> AgentResult:
        ...
