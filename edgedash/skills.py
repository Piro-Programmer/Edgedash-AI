"""
skills.py — deterministic skill canonicalisation (steering rule 23).

No LLM calls. No network. No model judgement. Pure functions only.

Public API
----------
    canonical(raw: str, aliases: dict[str, str]) -> str

CLI
---
    python -m edgedash.skills --audit
    Reads every extraction_cache row in the database and prints a ranked
    audit report so you can find missing aliases.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Compiled patterns — module-level so they are compiled exactly once.
# ---------------------------------------------------------------------------

# Strips a trailing parenthetical qualifier:
#   "kubernetes (eks)"  ->  "kubernetes "
#   "python (3.x)"      ->  "python "
_PAREN_RE = re.compile(r"\s*\([^)]*\)")

# Collapses any run of whitespace (spaces, tabs, newlines) to a single space.
_WHITESPACE_RE = re.compile(r"\s+")

# Characters to strip from the outer edges of a raw skill string.
_STRIP_CHARS = " \t\n.,;:\"'"


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def canonical(raw: str, aliases: dict[str, str]) -> str:
    """Return the canonical form of a raw skill string.

    Steps applied in order:
      1. Lowercase.
      2. Strip leading/trailing whitespace and punctuation.
      3. Drop parenthetical qualifiers  e.g. "(eks)", "(3.x)".
      4. Collapse internal whitespace runs to a single space.
      5. Apply the alias map — if the normalised string has an entry, use it.

    The function is pure: same input always produces the same output.
    An empty string (or one that is only punctuation) returns "".

    Parameters
    ----------
    raw:     The raw skill string, exactly as the extractor produced it.
    aliases: A dict mapping normalised raw forms to canonical forms.
             Comes from config.skill_aliases — never built inside this function.
    """
    if not raw:
        return ""

    s = raw.lower()
    s = s.strip(_STRIP_CHARS)
    s = _PAREN_RE.sub("", s)
    s = _WHITESPACE_RE.sub(" ", s).strip()

    return aliases.get(s, s)


# ---------------------------------------------------------------------------
# CLI audit
# ---------------------------------------------------------------------------

def _audit(db_path: str, aliases: dict[str, str]) -> None:
    """Print a skill-frequency audit report from the extraction cache.

    Reads extraction_cache only — never touches listings or any write path.
    """
    import collections
    import json
    import sqlite3

    # Read raw (already-normalised-to-lowercase) skill strings from cache.
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT result_json FROM extraction_cache").fetchall()
    conn.close()

    if not rows:
        print("No extraction cache rows found. Run the Fetcher + Scorer first.")
        return

    raw_counts: collections.Counter[str] = collections.Counter()
    for (result_json,) in rows:
        try:
            data = json.loads(result_json)
        except json.JSONDecodeError:
            continue
        for skill in data.get("required_skills", []) + data.get("nice_to_have", []):
            raw_counts[skill.lower().strip()] += 1

    if not raw_counts:
        print("Cache contains no skill data.")
        return

    # Split into multi-seen and singleton groups.
    multi  = [(sk, cnt) for sk, cnt in raw_counts.most_common() if cnt > 1]
    single = sorted(sk for sk, cnt in raw_counts.items() if cnt == 1)

    _print_top(multi[:40], aliases)
    _print_singletons(single)


def _print_top(
    skills: list[tuple[str, int]],
    aliases: dict[str, str],
) -> None:
    # Pass an empty alias dict to canonical() to get the normalised-only form,
    # then apply aliases manually so we can show both columns.
    no_alias_form = {sk: canonical(sk, {}) for sk, _ in skills}

    width_skill = max((len(sk) for sk, _ in skills), default=10)
    width_canon = max((len(aliases.get(v, v)) for v in no_alias_form.values()), default=10)

    print("=" * 70)
    print(f"  TOP {len(skills)} RAW SKILL STRINGS  (count | raw | canonical)")
    print("=" * 70)
    print(f"  {'count':>5}  {'raw skill':<{width_skill}}  canonical")
    print(f"  {'─'*5}  {'─'*width_skill}  {'─'*width_canon}")
    for raw_sk, cnt in skills:
        normed  = no_alias_form[raw_sk]
        canon   = aliases.get(normed, normed)
        aliased = "  ← aliased" if canon != normed else ""
        print(f"  {cnt:>5}  {raw_sk:<{width_skill}}  {canon}{aliased}")
    print()


def _print_singletons(singles: list[str]) -> None:
    print("=" * 70)
    print(f"  SINGLETONS — seen exactly once ({len(singles)} total)")
    print("  These are likely typos, junk, or full sentences captured as skills.")
    print("  Add an alias in config.yaml if any should map to a real skill.")
    print("=" * 70)
    for sk in singles:
        # Flag suspiciously long entries — probably extractor noise.
        flag = "  ⚠ long" if len(sk) > 40 else ""
        print(f"  {sk}{flag}")
    print()


if __name__ == "__main__":
    import sys
    from pathlib import Path

    if "--audit" not in sys.argv:
        print("Usage: python -m edgedash.skills --audit")
        raise SystemExit(1)

    # Load config so we use the real db_path and real alias map.
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from edgedash.config import load_config
    cfg = load_config()

    aliases: dict[str, str] = getattr(cfg, "skill_aliases", {})
    _audit(cfg.db_path, aliases)
