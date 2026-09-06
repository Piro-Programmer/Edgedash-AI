"""
run_cycle.py — entry point for a single EdgeDash cycle.

Usage:
    python run_cycle.py
"""

import sys

from edgedash.config import load_config
from edgedash.orchestrator import run_cycle

import sys
import io

if __name__ == "__main__":
    if sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    config = load_config()
    outcome = run_cycle(config)

    # Surface a bad cycle to the caller (the scheduler) using GitHub Actions warnings
    # instead of exiting 1, so the cron run stays green but the degraded state is visible.
    if outcome == "degraded":
        print("Cycle finished DEGRADED — verification failed after retry.")
        print("::warning::Cycle finished DEGRADED — verification failed. Check the cycle log for details.")
        sys.exit(0)
