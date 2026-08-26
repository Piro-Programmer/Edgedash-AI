"""
config.py — loads and validates user configuration from config.yaml.

All user-specific values (role, city, skills, etc.) live here.
No other module should reference these values directly.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    sys.exit(
        "PyYAML is required to parse config.yaml.  "
        "Install it with:  pip install pyyaml"
    )

# ---------------------------------------------------------------------------
# Default values — every field falls back to these when absent from the file.
# ---------------------------------------------------------------------------
_DEFAULTS: dict = {
    "target_role": "Software Engineer",
    "target_city": "Remote",
    "keywords": [],
    "my_skills": [],
    "experience_years": 0,
    "db_path": "edgedash.db",
    "min_fit_score": 50,
    "sources": ["arbeitnow"],
    "use_mock_fetcher": False,
    "llm_provider": "gemini",
    "llm_model": "gemini-1.5-flash",
    "llm_batch_size": 25,
    "score_batch_size": 50,
    "target_seniority": "mid",
    "weight_skill_match": 0.45,
    "weight_seniority_fit": 0.25,
    "weight_location_fit": 0.15,
    "weight_recency": 0.15,
    "skill_aliases": {},
    "fetch_interval_hours": 6,
    "fetch_max_pages": 5,
    "fetch_max_listings": 200,
    "score_max_seconds": 300,
    "analyse_max_seconds": 60,
}

_CONFIG_FILENAME = "config.yaml"


@dataclass
class Config:
    target_role: str
    target_city: str
    keywords: list[str]
    my_skills: list[str]
    experience_years: int
    db_path: str
    min_fit_score: int
    sources: list[str] = field(default_factory=lambda: ["arbeitnow"])
    use_mock_fetcher: bool = False
    llm_provider: str = "gemini"
    llm_model: str = "gemini-3.5-flash"
    llm_batch_size: int = 25
    score_batch_size: int = 50
    target_seniority: str = "mid"
    weight_skill_match: float = 0.45
    weight_seniority_fit: float = 0.25
    weight_location_fit: float = 0.15
    weight_recency: float = 0.15
    skill_aliases: dict[str, str] = field(default_factory=dict)
    fetch_interval_hours: float = 6.0
    fetch_max_pages: int = 5
    fetch_max_listings: int = 200
    score_max_seconds: int = 300
    analyse_max_seconds: int = 60


def load_config(repo_root: Path | None = None) -> Config:
    """Read config.yaml from *repo_root* (defaults to cwd) and return a Config.

    Raises FileNotFoundError with a clear message if config.yaml is absent.
    Raises ValueError if a field contains an unexpected type.
    """
    root = repo_root or Path.cwd()
    config_path = root / _CONFIG_FILENAME

    if not config_path.exists():
        raise FileNotFoundError(
            f"config.yaml not found at '{config_path}'.  "
            "Copy the example config.yaml from the repo root and edit it."
        )

    with config_path.open("r", encoding="utf-8") as fh:
        raw: dict = yaml.safe_load(fh) or {}

    data = {**_DEFAULTS, **raw}

    _validate(data, config_path)

    return Config(
        target_role=str(data["target_role"]),
        target_city=str(data["target_city"]),
        keywords=_as_str_list(data["keywords"], "keywords"),
        my_skills=_as_str_list(data["my_skills"], "my_skills"),
        experience_years=_as_int(data["experience_years"], "experience_years"),
        db_path=str(data["db_path"]),
        min_fit_score=_as_int(data["min_fit_score"], "min_fit_score"),
        sources=_as_str_list(data["sources"], "sources"),
        use_mock_fetcher=bool(data["use_mock_fetcher"]),
        llm_provider=str(data["llm_provider"]),
        llm_model=str(data["llm_model"]),
        llm_batch_size=_as_int(data["llm_batch_size"], "llm_batch_size"),
        score_batch_size=_as_int(data["score_batch_size"], "score_batch_size"),
        target_seniority=str(data["target_seniority"]),
        weight_skill_match=float(data["weight_skill_match"]),
        weight_seniority_fit=float(data["weight_seniority_fit"]),
        weight_location_fit=float(data["weight_location_fit"]),
        weight_recency=float(data["weight_recency"]),
        skill_aliases=_as_str_dict(data["skill_aliases"], "skill_aliases"),
        fetch_interval_hours=float(data["fetch_interval_hours"]),
        fetch_max_pages=_as_int(data["fetch_max_pages"], "fetch_max_pages"),
        fetch_max_listings=_as_int(data["fetch_max_listings"], "fetch_max_listings"),
        score_max_seconds=_as_int(data["score_max_seconds"], "score_max_seconds"),
        analyse_max_seconds=_as_int(data["analyse_max_seconds"], "analyse_max_seconds"),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _validate(data: dict, path: Path) -> None:
    required = ["target_role", "target_city"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise ValueError(
            f"config.yaml at '{path}' is missing required fields: {missing}"
        )


def _as_str_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(
            f"config.yaml: '{field_name}' must be a list of strings, "
            f"got {type(value).__name__}."
        )
    return [str(item) for item in value]


def _as_int(value: object, field_name: str) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"config.yaml: '{field_name}' must be an integer, "
            f"got '{value}'."
        ) from exc


def _as_str_dict(value: object, field_name: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(
            f"config.yaml: '{field_name}' must be a mapping, "
            f"got {type(value).__name__}."
        )
    return {str(k): str(v) for k, v in value.items()}
