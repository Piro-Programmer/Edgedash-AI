"""
http.py — The ONLY module permitted to make HTTP requests in EdgeDash.

Provides a single helper get_json() that enforces:
  - 10-second timeout (configurable)
  - 2 retries with exponential backoff (1s, 2s)
  - A descriptive User-Agent header
  - Raises SourceError on any failure

No other module may call requests.get() or any HTTP library directly.
"""

from __future__ import annotations

import time
from typing import Any

import requests

_USER_AGENT = (
    "EdgeDash/0.1 (career intelligence agent; "
    "github.com/edgedash; contact via repo)"
)

_DEFAULT_TIMEOUT = 10       # seconds
_MAX_RETRIES = 2
_BACKOFF_BASE = 1.0         # seconds; doubles on each retry


class SourceError(Exception):
    """Raised when an HTTP request to a job source fails after all retries."""


def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> Any:
    """Fetch JSON from *url* with retry and backoff.

    Returns the decoded JSON payload on success.
    Raises SourceError after _MAX_RETRIES + 1 total attempts.
    """
    merged_headers = {"User-Agent": _USER_AGENT}
    if headers:
        merged_headers.update(headers)

    last_exc: Exception | None = None
    attempts = _MAX_RETRIES + 1

    for attempt in range(attempts):
        if attempt > 0:
            sleep_seconds = _BACKOFF_BASE * (2 ** (attempt - 1))
            time.sleep(sleep_seconds)

        try:
            response = requests.get(
                url,
                params=params,
                headers=merged_headers,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as exc:
            last_exc = exc
            # continue to next retry

    raise SourceError(
        f"Failed to fetch {url!r} after {attempts} attempt(s). "
        f"Last error: {last_exc}"
    ) from last_exc
