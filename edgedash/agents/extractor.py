"""
extractor.py — extracts structured facts from a job description.

This is the ONLY part of the scoring pipeline that calls a model.
It returns raw facts; it never scores, ranks, or evaluates fit.

Public API
----------
    extract(listing: dict, db_path: str) -> dict

The returned dict always matches EXTRACTION_SCHEMA.  Callers (the Scorer)
are responsible for what they do with those facts.
"""

from __future__ import annotations

import hashlib

import edgedash.storage as storage
from edgedash.llm import LLMError, complete_json

# ---------------------------------------------------------------------------
# Extraction schema (rule 16 — no score field, ever)
# ---------------------------------------------------------------------------

EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "required": [
        "required_skills",
        "nice_to_have",
        "seniority",
        "years_required",
        "remote_ok",
    ],
    "additionalProperties": False,
    "properties": {
        "required_skills": {
            "type": "array",
            "items": {"type": "string"},
        },
        "nice_to_have": {
            "type": "array",
            "items": {"type": "string"},
        },
        "seniority": {
            "type": "string",
            "enum": ["junior", "mid", "senior", "lead", "unknown"],
        },
        "years_required": {
            # null is represented as JSON null; jsonschema "type" list handles it.
            "type": ["integer", "null"],
        },
        "remote_ok": {
            "type": ["boolean", "null"],
        },
    },
}

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are reading a job listing document. Your task is to extract structured
facts that are explicitly stated in the text below.

Rules:
- Report only what the listing states. Do not infer, guess, or evaluate.
- If a piece of information is not mentioned, use null (for scalar fields)
  or an empty list (for list fields).
- There is no candidate. Do not consider fit, suitability, or match.
- For seniority: choose from ["junior","mid","senior","lead","unknown"].
  Use "unknown" unless the listing uses one of those terms or an obvious
  equivalent (e.g. "entry-level" -> "junior", "staff" -> "lead").
- For years_required: extract the minimum years-of-experience as an integer
  only if the listing states it explicitly. Use null otherwise — never guess.
- For remote_ok: true if the listing explicitly says remote is allowed,
  false if it explicitly says on-site / office only, null if not stated.

Output must be a JSON object with EXACTLY these five keys — no others:
  required_skills  (array of strings) — skills the role explicitly requires
  nice_to_have     (array of strings) — skills listed as preferred or a bonus
  seniority        (string)           — one of: junior, mid, senior, lead, unknown
  years_required   (integer or null)  — minimum years stated, or null
  remote_ok        (boolean or null)  — true/false/null

--- JOB LISTING ---
{description}
--- END ---"""


def _build_prompt(description: str) -> str:
    return _PROMPT_TEMPLATE.format(description=description.strip())


# ---------------------------------------------------------------------------
# Hash helper
# ---------------------------------------------------------------------------

def _description_hash(text: str) -> str:
    """Return a stable SHA-256 hex digest of the description text."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise(result: dict) -> dict:
    """Lowercase all skill names in-place; return the same dict."""
    result["required_skills"] = [s.lower() for s in result["required_skills"]]
    result["nice_to_have"] = [s.lower() for s in result["nice_to_have"]]
    return result


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def extract(listing: dict, db_path: str) -> dict:
    """Extract structured facts from *listing* and return a validated dict.

    Cache behaviour (rule 18):
      - Computes a SHA-256 hash of the raw description text.
      - On a cache hit: returns the stored result immediately, no model call.
      - On a cache miss: calls the model, normalises, stores, then returns.

    On model failure the LLMError propagates to the caller (the Scorer),
    which logs it as a per-listing failure per rule 17 — it must not crash
    the full scoring cycle.

    Parameters
    ----------
    listing:  A dict with at least a "description" key (may be None/empty).
    db_path:  Path to the SQLite database, forwarded to storage functions.
    """
    description: str = listing.get("description") or ""

    # Empty descriptions cannot be extracted meaningfully.
    if not description.strip():
        return _empty_result()

    desc_hash = _description_hash(description)

    # --- Cache check ---
    cached = storage.get_extraction_cache(db_path, desc_hash)
    if cached is not None:
        return cached

    # --- Model call ---
    prompt = _build_prompt(description)
    result = complete_json(prompt, EXTRACTION_SCHEMA, max_retries=1)

    # Normalise skill names to lowercase before caching (rule: consistent matching).
    result = _normalise(result)

    # --- Store in cache ---
    storage.set_extraction_cache(db_path, desc_hash, result)

    return result


def _empty_result() -> dict:
    """Return a valid, empty extraction result for listings with no description."""
    return {
        "required_skills": [],
        "nice_to_have": [],
        "seniority": "unknown",
        "years_required": None,
        "remote_ok": None,
    }
