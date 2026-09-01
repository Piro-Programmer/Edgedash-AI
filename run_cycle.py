"""
run_cycle.py — entry point for a single EdgeDash cycle.

Usage:
    python run_cycle.py
"""

import sys

from edgedash.config import load_config
from edgedash.orchestrator import run_cycle

if __name__ == "__main__":
    config = load_config()
    outcome = run_cycle(config)

    # Surface a bad cycle to the caller (the scheduler) instead of always
    # exiting 0 — a silently green cron run hides a degraded pipeline.
    if outcome == "degraded":
        print("Cycle finished DEGRADED — verification failed after retry.")
        sys.exit(1)
