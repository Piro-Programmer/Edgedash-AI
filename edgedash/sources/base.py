"""
base.py — Source protocol and registry for EdgeDash job sources.

Every source must satisfy the Source protocol:
  - a `name` class attribute identifying the source in logs
  - a `fetch(config)` method returning a list of normalised dicts

Normalised dict keys (steering rule 10):
  source, external_id, title, company, location, url,
  description, posted_at, raw

Missing values must be None, never empty string, never "N/A".

The SOURCES registry and @register decorator allow a new source to
be added by decorating its class — nothing else needs to change.
"""

from __future__ import annotations

from typing import Any, Protocol, Type, runtime_checkable

from edgedash.config import Config

# ---------------------------------------------------------------------------
# Normalised row keys — every source must return dicts with exactly these keys
# ---------------------------------------------------------------------------
NORMALISED_KEYS: tuple[str, ...] = (
    "source",
    "external_id",
    "title",
    "company",
    "location",
    "url",
    "description",
    "posted_at",
    "raw",
)


@runtime_checkable
class Source(Protocol):
    name: str

    def fetch(self, config: Config) -> list[dict[str, Any]]:
        """Return a list of normalised job dicts."""
        ...


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

SOURCES: dict[str, Type[Source]] = {}


def register(cls: Type[Source]) -> Type[Source]:
    """Class decorator that registers a Source in the global SOURCES dict."""
    SOURCES[cls.name] = cls
    return cls


# ---------------------------------------------------------------------------
# Validation helper
# ---------------------------------------------------------------------------

def validate_row(row: dict[str, Any], source_name: str) -> None:
    """Raise ValueError if a normalised row is missing required keys."""
    missing = [k for k in NORMALISED_KEYS if k not in row]
    if missing:
        raise ValueError(
            f"Source '{source_name}' returned a row missing keys: {missing}"
        )
