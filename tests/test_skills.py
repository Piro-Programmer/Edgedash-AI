"""
tests/test_skills.py — unit tests for edgedash.skills.canonical().

canonical() is a pure function with no I/O. Every case is table-driven
and deterministic. No fixtures, no mocks, no network.
"""

import pytest
from edgedash.skills import canonical

# A small alias dict reused across tests — mirrors the real config shape.
_ALIASES = {
    "k8s":                   "kubernetes",
    "gke":                   "kubernetes",
    "google cloud platform": "gcp",
    "google cloud":          "gcp",
    "node":                  "node.js",
    "nodejs":                "node.js",
    "postgresql":            "postgres",
    "psql":                  "postgres",
    "ci cd":                 "ci/cd",
    "cicd":                  "ci/cd",
    "ml":                    "machine learning",
    "nestjs":                "nest.js",
    "nextjs":                "next.js",
}


# ---------------------------------------------------------------------------
# 1. Lowercase normalisation
# ---------------------------------------------------------------------------

class TestLowercase:
    def test_all_caps(self):
        assert canonical("PYTHON", _ALIASES) == "python"

    def test_mixed_case(self):
        assert canonical("TypeScript", _ALIASES) == "typescript"

    def test_already_lower(self):
        assert canonical("sql", _ALIASES) == "sql"


# ---------------------------------------------------------------------------
# 2. Whitespace handling
# ---------------------------------------------------------------------------

class TestWhitespace:
    def test_leading_trailing_spaces(self):
        assert canonical("  python  ", _ALIASES) == "python"

    def test_internal_multiple_spaces(self):
        assert canonical("machine  learning", _ALIASES) == "machine learning"

    def test_tab_between_words(self):
        assert canonical("machine\tlearning", _ALIASES) == "machine learning"

    def test_newline_inside(self):
        assert canonical("big\ndata", _ALIASES) == "big data"


# ---------------------------------------------------------------------------
# 3. Parenthetical qualifier stripping
# ---------------------------------------------------------------------------

class TestParentheses:
    def test_trailing_qualifier(self):
        assert canonical("kubernetes (eks)", _ALIASES) == "kubernetes"

    def test_version_qualifier(self):
        assert canonical("python (3.x)", _ALIASES) == "python"

    def test_qualifier_with_inner_spaces(self):
        assert canonical("aws (amazon web services)", _ALIASES) == "aws"

    def test_no_parens_unchanged(self):
        assert canonical("docker", _ALIASES) == "docker"

    def test_parens_then_alias(self):
        # After stripping "(managed)" we get "kubernetes", which already
        # maps to itself — but "k8s (managed)" should strip to "k8s" -> "kubernetes".
        assert canonical("k8s (managed)", _ALIASES) == "kubernetes"


# ---------------------------------------------------------------------------
# 4. Outer punctuation stripping
# ---------------------------------------------------------------------------

class TestOuterPunctuation:
    def test_trailing_period(self):
        assert canonical("python.", _ALIASES) == "python"

    def test_surrounding_quotes(self):
        assert canonical('"sql"', _ALIASES) == "sql"

    def test_trailing_comma(self):
        assert canonical("docker,", _ALIASES) == "docker"

    def test_surrounding_semicolons(self):
        assert canonical(";rust;", _ALIASES) == "rust"


# ---------------------------------------------------------------------------
# 5. Alias lookup — known aliases
# ---------------------------------------------------------------------------

class TestAliasedTerms:
    @pytest.mark.parametrize("raw,expected", [
        ("k8s",                   "kubernetes"),
        ("K8S",                   "kubernetes"),   # alias lookup after lowercasing
        ("gke",                   "kubernetes"),
        ("google cloud platform", "gcp"),
        ("Google Cloud Platform", "gcp"),
        ("node",                  "node.js"),
        ("nodejs",                "node.js"),
        ("Node.JS",               "node.js"),      # already canonical after lower — no alias needed
        ("postgresql",            "postgres"),
        ("psql",                  "postgres"),
        ("cicd",                  "ci/cd"),
        ("ci cd",                 "ci/cd"),
        ("ml",                    "machine learning"),
        ("nestjs",                "nest.js"),
        ("nextjs",                "next.js"),
    ])
    def test_alias_resolves(self, raw: str, expected: str):
        assert canonical(raw, _ALIASES) == expected


# ---------------------------------------------------------------------------
# 6. Terms with no alias — pass-through unchanged (after normalisation)
# ---------------------------------------------------------------------------

class TestNoAlias:
    @pytest.mark.parametrize("raw,expected", [
        ("terraform",   "terraform"),
        ("react",       "react"),
        ("typescript",  "typescript"),
        ("docker",      "docker"),
        ("bigquery",    "bigquery"),
    ])
    def test_passthrough(self, raw: str, expected: str):
        assert canonical(raw, _ALIASES) == expected

    def test_empty_alias_dict(self):
        """An empty alias dict must not crash and must still normalise."""
        assert canonical("K8s (managed)", {}) == "k8s"


# ---------------------------------------------------------------------------
# 7. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string(self):
        assert canonical("", _ALIASES) == ""

    def test_whitespace_only(self):
        assert canonical("   ", _ALIASES) == ""

    def test_punctuation_only(self):
        assert canonical("...,,,", _ALIASES) == ""

    def test_single_character(self):
        assert canonical("R", _ALIASES) == "r"

    def test_alias_with_slash_preserved(self):
        # "ci/cd" is the canonical form; it contains a slash which must survive.
        assert canonical("ci/cd", _ALIASES) == "ci/cd"

    def test_node_js_not_javascript(self):
        """node.js and javascript must remain distinct — rule 2 of the brief."""
        result_node = canonical("node", _ALIASES)
        result_js   = canonical("javascript", _ALIASES)
        assert result_node == "node.js"
        assert result_js   == "javascript"
        assert result_node != result_js

    def test_same_input_same_output(self):
        """Idempotency: calling canonical twice must produce the same result."""
        first  = canonical("  K8s (eks)  ", _ALIASES)
        second = canonical(first, _ALIASES)
        assert first == second == "kubernetes"
