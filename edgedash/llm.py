"""
llm.py — the single door to any language model (steering rule 15).

Public API
----------
    complete_json(prompt, schema, *, max_retries=1) -> dict

Everything else is internal.  No other module may import an LLM SDK.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from typing import Any, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Raised when the LLM gateway cannot return a valid response."""


# ---------------------------------------------------------------------------
# Rate-limiter (rule 15: ≥1 s between calls, ≤15 calls / 60 s)
# ---------------------------------------------------------------------------

_MIN_INTERVAL: float = 1.0          # seconds between consecutive calls
_ROLLING_WINDOW: float = 60.0       # seconds in the rolling window
_ROLLING_CAP: int = 15              # max calls inside that window
_last_call_time: float = 0.0
_call_timestamps: deque[float] = deque()


def _rate_limit() -> None:
    """Block until both rate constraints are satisfied, then record the call."""
    global _last_call_time

    while True:
        now = time.monotonic()

        # Drop timestamps outside the rolling window.
        while _call_timestamps and now - _call_timestamps[0] > _ROLLING_WINDOW:
            _call_timestamps.popleft()

        gap_ok = (now - _last_call_time) >= _MIN_INTERVAL
        cap_ok = len(_call_timestamps) < _ROLLING_CAP

        if gap_ok and cap_ok:
            break

        # Sleep the minimum time needed to satisfy whichever constraint fires.
        sleeps: list[float] = []
        if not gap_ok:
            sleeps.append(_MIN_INTERVAL - (now - _last_call_time))
        if not cap_ok:
            # Oldest timestamp will drop out of the window after this long.
            sleeps.append(_ROLLING_WINDOW - (now - _call_timestamps[0]))

        time.sleep(max(0.0, min(sleeps)))

    _last_call_time = time.monotonic()
    _call_timestamps.append(_last_call_time)


# ---------------------------------------------------------------------------
# JSON extraction helpers
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def _extract_json(text: str) -> dict:
    """Strip markdown fences and leading/trailing prose, then parse JSON.

    Raises ValueError if no valid JSON object is found.
    """
    # 1. Try to pull content from a fenced block first.
    fence_match = _FENCE_RE.search(text)
    candidate = fence_match.group(1).strip() if fence_match else text.strip()

    # 2. Find the first '{' and last '}' to discard surrounding prose.
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object found in model response: {text!r}")

    return json.loads(candidate[start : end + 1])


def _validate(data: dict, schema: dict) -> None:
    """Minimal structural validation against a JSON-Schema-like dict.

    Supports: type, properties (required presence + type), required list.
    Uses jsonschema when available; falls back to hand-rolled checks so the
    module works even without the optional dependency.
    """
    try:
        import jsonschema  # optional
        jsonschema.validate(data, schema)
    except ImportError:
        _validate_lite(data, schema)


def _validate_lite(data: dict, schema: dict) -> None:
    """Hand-rolled subset of JSON Schema validation (no extra dependency)."""
    required: list[str] = schema.get("required", [])
    for key in required:
        if key not in data:
            raise ValueError(f"Missing required field in model response: '{key}'")

    properties: dict = schema.get("properties", {})
    for key, prop in properties.items():
        if key not in data:
            continue
        expected_type = prop.get("type")
        if not expected_type:
            continue
        _type_map = {
            "string": str, "number": (int, float),
            "integer": int, "boolean": bool,
            "array": list, "object": dict,
        }
        # "type" may be a list of strings e.g. ["integer", "null"] — handle both.
        if isinstance(expected_type, list):
            # null in a type list means None is acceptable.
            if data[key] is None and "null" in expected_type:
                continue
            py_types = tuple(
                _type_map[t] for t in expected_type
                if t != "null" and t in _type_map
            )
            if py_types and not isinstance(data[key], py_types):
                raise ValueError(
                    f"Field '{key}' expected one of types {expected_type}, "
                    f"got {type(data[key]).__name__}"
                )
        else:
            # null value is acceptable for any nullable field at schema level.
            if data[key] is None:
                continue
            py_type = _type_map.get(expected_type)
            if py_type and not isinstance(data[key], py_type):
                raise ValueError(
                    f"Field '{key}' expected type '{expected_type}', "
                    f"got {type(data[key]).__name__}"
                )


# ---------------------------------------------------------------------------
# Provider protocol + implementations
# ---------------------------------------------------------------------------

@runtime_checkable
class _Provider(Protocol):
    def call(self, prompt: str) -> str:
        """Send *prompt* and return the raw text reply."""
        ...


class _GeminiProvider:
    """google-genai backend (new unified SDK)."""

    def __init__(self, model: str) -> None:
        try:
            from google import genai  # type: ignore[import]
        except ImportError as exc:
            raise LLMError(
                "google-genai is not installed. "
                "Run: pip install google-genai"
            ) from exc

        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LLMError(
                "GEMINI_API_KEY environment variable is not set. "
                "Add it to your .env file (see .env.example) and "
                "make sure python-dotenv is loading it before calling the LLM."
            )

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def call(self, prompt: str) -> str:
        backoff = 2.0
        for attempt in range(3):
            try:
                # Use chats API — avoids the AFC warning and is the
                # recommended path for the new google-genai SDK.
                chat = self._client.chats.create(model=self._model)
                response = chat.send_message(prompt)
                return response.text
            except Exception as exc:
                msg = str(exc).lower()
                is_quota = "429" in msg or "quota" in msg or "resource_exhausted" in msg
                if is_quota and attempt < 2:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise LLMError(f"Gemini API error: {exc}") from exc
        raise LLMError("Gemini quota exhausted after 3 backoff attempts.")


class _OllamaProvider:
    """Local Ollama HTTP backend — no API key required."""

    _DEFAULT_BASE = "http://localhost:11434"

    def __init__(self, model: str) -> None:
        self._model = model
        self._base = os.environ.get("OLLAMA_BASE_URL", self._DEFAULT_BASE).rstrip("/")

    def call(self, prompt: str) -> str:
        import urllib.request

        payload = json.dumps({
            "model": self._model,
            "prompt": prompt,
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            f"{self._base}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        backoff = 2.0
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    body = json.loads(resp.read())
                    return str(body.get("response", ""))
            except Exception as exc:
                msg = str(exc).lower()
                is_quota = "429" in msg or "too many" in msg
                if is_quota and attempt < 2:
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise LLMError(f"Ollama error: {exc}") from exc
        raise LLMError("Ollama unreachable after 3 backoff attempts.")  # unreachable


# ---------------------------------------------------------------------------
# Provider registry — adding a third provider touches only this dict
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY: dict[str, type] = {
    "gemini": _GeminiProvider,
    "ollama": _OllamaProvider,
}


def _make_provider(provider_name: str, model: str) -> _Provider:
    cls = _PROVIDER_REGISTRY.get(provider_name.lower())
    if cls is None:
        supported = ", ".join(f'"{k}"' for k in _PROVIDER_REGISTRY)
        raise LLMError(
            f"Unknown llm_provider '{provider_name}'. "
            f"Supported values: {supported}. "
            "Update config.yaml to use one of them."
        )
    return cls(model)


# Module-level cached provider — built once per process from config.
_provider: _Provider | None = None


def _get_provider() -> _Provider:
    """Return the cached provider, building it from config on first call."""
    global _provider
    if _provider is None:
        _load_dotenv()
        from edgedash.config import load_config
        cfg = load_config()
        _provider = _make_provider(cfg.llm_provider, cfg.llm_model)
    return _provider


# ---------------------------------------------------------------------------
# Public function
# ---------------------------------------------------------------------------

def complete_json(
    prompt: str,
    schema: dict,
    *,
    max_retries: int = 1,
) -> dict:
    """Send *prompt* to the configured LLM and return a validated dict.

    The response is parsed as JSON and validated against *schema* before
    being returned.  If parsing or validation fails, one retry is made with
    an appended correction instruction.  A second failure raises LLMError.

    Parameters
    ----------
    prompt:      The user-facing prompt.  Do not include JSON instructions —
                 this function appends them automatically.
    schema:      A JSON-Schema-compatible dict used to validate the response.
    max_retries: How many times to retry on parse/validation failure (default 1).
    """
    provider = _get_provider()
    json_instruction = (
        "\n\nRespond with a single JSON object only. "
        "No markdown, no code fences, no prose before or after."
    )
    full_prompt = prompt + json_instruction

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        if attempt > 0 and last_error is not None:
            repair_note = (
                f"\n\nYour previous response failed validation: {last_error}\n"
                "Reply with a corrected JSON object only. "
                "No markdown, no code fences, no prose."
            )
            full_prompt = prompt + json_instruction + repair_note

        _rate_limit()

        try:
            raw = provider.call(full_prompt)
        except LLMError:
            raise  # quota / network errors propagate immediately

        try:
            data = _extract_json(raw)
            _validate(data, schema)
            return data
        except (ValueError, json.JSONDecodeError, Exception) as exc:
            last_error = exc
            # Loop once more if retries remain; otherwise fall through.

    raise LLMError(
        f"LLM response failed validation after {max_retries + 1} attempt(s). "
        f"Last error: {last_error}"
    )


# ---------------------------------------------------------------------------
# CLI check:  python -m edgedash.llm --check
# ---------------------------------------------------------------------------

def _load_dotenv() -> None:
    """Load .env from the repo root into os.environ if python-dotenv is available."""
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        from pathlib import Path
        load_dotenv(Path(__file__).parent.parent / ".env", override=False)
    except ImportError:
        pass  # dotenv is optional; users can export vars manually


def _cli_check() -> None:
    """Send one trivial prompt and print provider, model, and result."""
    _load_dotenv()
    from edgedash.config import load_config

    cfg = load_config()
    print(f"Provider : {cfg.llm_provider}")
    print(f"Model    : {cfg.llm_model}")
    print("Sending  : trivial ping prompt …")

    schema = {
        "type": "object",
        "required": ["ok"],
        "properties": {"ok": {"type": "boolean"}},
    }
    try:
        result = complete_json(
            'Reply with exactly: {"ok": true}',
            schema,
        )
        print(f"Response : {result}")
        print("Status   : OK")
    except LLMError as exc:
        print(f"Status   : FAILED — {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    import sys
    if "--check" in sys.argv:
        _cli_check()
    else:
        print("Usage: python -m edgedash.llm --check")
        raise SystemExit(1)
