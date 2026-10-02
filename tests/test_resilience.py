"""
Regression tests for the production 'partial' verdict (Oct 2026).

Gemini returned 503 UNAVAILABLE for the head-of-queue listing every cycle.
llm.py treated it as fatal, and with llm_batch_size=1 the Scorer retried
that same listing forever while 600+ listings stayed unscored.
"""

from __future__ import annotations

import pytest

import edgedash.llm as llm
import edgedash.agents.scorer as scorer_mod
from edgedash.agents.scorer import Scorer
from edgedash.planning import StopConditions


# ---------------------------------------------------------------------------
# llm.py — transient errors are retried
# ---------------------------------------------------------------------------

_OVERLOADED = (
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is "
    "currently experiencing high demand.', 'status': 'UNAVAILABLE'}}"
)


@pytest.mark.parametrize("msg", [
    _OVERLOADED,
    "500 INTERNAL",
    "504 DEADLINE_EXCEEDED",
    "The model is overloaded",
])
def test_transient_errors_detected(msg):
    assert llm._is_transient_error(msg)


@pytest.mark.parametrize("msg", [
    "400 INVALID_ARGUMENT",
    "404 NOT_FOUND model is no longer available",
    "403 PERMISSION_DENIED",
])
def test_permanent_errors_not_transient(msg):
    assert not llm._is_transient_error(msg)


class _FlakyChat:
    def __init__(self, failures: list[Exception]):
        self._failures = failures

    def send_message(self, prompt):
        if self._failures:
            raise self._failures.pop(0)
        return type("R", (), {"text": '{"ok": true}'})()


def _provider_with(failures: list[Exception]) -> llm._GeminiProvider:
    p = llm._GeminiProvider.__new__(llm._GeminiProvider)
    p._model = "test-model"
    chat = _FlakyChat(failures)
    p._client = type("C", (), {
        "chats": type("Chats", (), {"create": staticmethod(lambda model: chat)})()
    })()
    return p


def test_gemini_retries_503_then_succeeds(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    p = _provider_with([Exception(_OVERLOADED), Exception(_OVERLOADED)])
    assert p.call("x") == '{"ok": true}'


def test_gemini_does_not_retry_permanent_error(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(llm.time, "sleep", sleeps.append)
    p = _provider_with([Exception("400 INVALID_ARGUMENT"), Exception("unused")])
    with pytest.raises(llm.LLMError):
        p.call("x")
    assert sleeps == []


def test_gemini_gives_up_after_max_attempts(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    p = _provider_with([Exception(_OVERLOADED)] * llm._GEMINI_ATTEMPTS)
    with pytest.raises(llm.LLMError):
        p.call("x")


# ---------------------------------------------------------------------------
# scorer.py — one failing listing does not block the queue
# ---------------------------------------------------------------------------

def _listings(n: int) -> list[dict]:
    return [{"id": f"id{i:02d}" + "0" * 14, "title": f"Job {i}"} for i in range(n)]


@pytest.fixture
def fake_scoring(monkeypatch):
    written: list[str] = []
    monkeypatch.setattr(scorer_mod.storage, "log_cycle", lambda **kw: None)
    monkeypatch.setattr(
        scorer_mod.storage, "write_score",
        lambda **kw: written.append(kw["listing_id"]),
    )
    monkeypatch.setattr(
        scorer_mod, "score_listing",
        lambda listing, facts, config: {"score": 50, "reason": "r", "components": {}},
    )
    return written


def _config(batch_size: int):
    return type("Cfg", (), {"llm_batch_size": batch_size, "score_max_seconds": None})()


def test_failing_head_listing_does_not_block_queue(monkeypatch, fake_scoring):
    rows = _listings(3)
    monkeypatch.setattr(
        scorer_mod.storage, "get_unscored_listings", lambda path, limit: rows[:limit]
    )

    def extract(listing, path, strict=False):
        if listing["id"] == rows[0]["id"]:
            raise llm.LLMError("Gemini API error: 503 UNAVAILABLE")
        return {}

    monkeypatch.setattr(scorer_mod, "extract", extract)

    result = Scorer().run(_config(1), "db", StopConditions(max_items=1))

    assert result.status == "ok"
    assert result.records_touched == 1
    assert fake_scoring == [rows[1]["id"]]


def test_scorer_stops_at_batch_size(monkeypatch, fake_scoring):
    rows = _listings(5)
    monkeypatch.setattr(
        scorer_mod.storage, "get_unscored_listings", lambda path, limit: rows[:limit]
    )
    monkeypatch.setattr(scorer_mod, "extract", lambda listing, path, strict=False: {})

    result = Scorer().run(_config(2), "db", StopConditions(max_items=2))

    assert result.records_touched == 2
    assert fake_scoring == [rows[0]["id"], rows[1]["id"]]
