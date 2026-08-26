---
inclusion: always
---

# EdgeDash — Project Steering Rules

## Project

EdgeDash is an autonomous AI career intelligence agent. It runs as a scheduled loop that fetches live job listings, scores them for fit against a user profile, surfaces skill gaps, verifies its own output, and publishes a Streamlit dashboard.

## Architecture

```
Trigger (scheduled)
  └─> Orchestrator
        ├─> Fetcher        (retrieves raw job listings)
        ├─> Scorer         (scores listings against profile)
        └─> GapAnalyzer    (identifies skill gaps)
              └─> Verifier
                    └─> Storage
                          └─> Dashboard (read-only)
```

**Do not deviate from this architecture without explaining the change first.**

- The Orchestrator reads state and delegates work. It never fetches data or scores listings directly.
- Each sub-agent has exactly one goal and one stop condition.
- The Dashboard is read-only; it never writes to storage.

## Hard Rules

### 1 — Python version and dependencies
- Target Python 3.11+.
- Prefer the standard library. Before adding any third-party dependency, state what it replaces and why the standard library is not sufficient.

### 2 — Storage isolation
- All storage access must go through a single `storage` module that exposes a thin interface.
- No other module may import `sqlite3` (or any DB driver) directly.
- The current backend is SQLite. Swapping to hosted Postgres in week 4 must require changes to exactly one file.

### 3 — No hardcoded user-specific values
- Role, city, keywords, skills profile, and any other user-specific configuration must never appear as literals in code.
- Everything user-specific lives in a config file or config object loaded from a single place.

### 4 — No secrets in code
- API keys, tokens, passwords, and credentials must never appear in source files.
- All secrets are read from environment variables, loaded in one place (e.g., a `settings` or `env` module).

### 5 — Cycle logging
- Every agent run must write a row to the `cycle_log` table recording:
  - which agent ran
  - start timestamp
  - number of records touched
  - pass/fail status
  - retry reason (if applicable)

### 6 — Fail loudly
- No bare `except: pass` or silent error swallowing.
- Unexpected errors must propagate or be logged with full context so they are visible.

### 7 — Type hints and docstrings
- Every function signature must have type hints on all parameters and the return type.
- Add docstrings only where the intent is not obvious from the function name.

### 8 — File size
- Keep individual files under approximately 150 lines.
- Split a module before it reaches that limit, not after.

## Network & Sources

### 9 — Source abstraction
- Every external job source lives behind a `Source` class with a uniform interface.
- The Fetcher contains no source-specific parsing logic. Adding a new source must never require editing the Fetcher.

### 10 — Normalised output contract
- Every `Source` returns a list of dicts with exactly these keys:
  `source`, `external_id`, `title`, `company`, `location`, `url`, `description`, `posted_at`, `raw`.
- Missing values are `None`. Never use empty string or `"N/A"` as a sentinel.

### 11 — One network helper
- All HTTP calls go through a single helper that enforces: 10-second timeout (default), 2 retry attempts with exponential backoff, and a `User-Agent` header.
- No bare `requests.get(...)` anywhere else in the codebase.

### 12 — Per-source fault isolation
- A source failing must never kill the cycle.
- Catch exceptions per-source, write a `cycle_log` row with `status = "failed"`, and continue to the next source.
- One dead job board must not stop the other sources from running.

### 13 — Secret management for sources
- Source credentials come from environment variables, loaded from a `.env` file that is gitignored.
- Never a literal key in code; never a key in `config.yaml`.
- If a required environment variable is absent, that source skips itself and writes a clear line to the log — it does not raise or crash the cycle.

### 14 — Respectful scraping
- Rate-limit to at most 1 request per second per source.
- Always send a descriptive, honest `User-Agent` header.
- Honour any documented page limits or `robots.txt` restrictions for the source.

## Style

- Write small, testable functions with a single responsibility.
- Prefer plain, readable Python over clever or overly concise Python.
- When asked to build one module, build that module only — do not scaffold the whole application.

## Intelligence & Scoring

### 15 — One LLM gateway
- All LLM calls go through a single module, `edgedash/llm.py`, which exposes one public function.
- The provider and model name come from config; they are never hardcoded.
- Rate-limit to stay inside a free tier: default 1 request per second, hard cap 15 per minute.
- No other file may import an LLM SDK directly.

### 16 — Model extracts facts; Python scores
- Never ask the model for a final score, ranking, or numeric rating.
- The model's only job is to extract structured facts from the job description.
- All scoring arithmetic lives in one deterministic Python function.
- The model never sees the scoring weights.

### 17 — Validate every model response
- Every model response is validated against an explicit schema before use.
- A response that fails validation is retried once, then logged as a failure for that listing only.
- A single bad response must not crash the cycle or skip the remaining listings.
- Never call `json.loads` on raw model text without a validation and repair path.

### 18 — Idempotent scoring with description-level caching
- Never re-score a listing that already has a score. Select only listings `WHERE score IS NULL`.
- Cache extraction results keyed on a hash of the job description so the same text is never sent to the model twice.

### 19 — Human-readable score reasons from code, not the model
- Every score carries a human-readable reason generated from the score components by our code.
- The model never writes free-text justifications that appear in the UI.

### 20 — Log score distribution on every run
- Log count, min, max, mean, and spread to `cycle_log` on every scoring run.
- A run where all scores fall within a 10-point range is a suspect run and must be flagged as such in the log.

### 21 — Configurable batch cap
- Cap the number of listings scored per cycle at a configurable batch size (default 25).
- This makes a cost or rate-limit blowup structurally impossible regardless of how many unscored listings accumulate.

## Aggregate Analysis

### 22 — Aggregates are deterministic SQL and Python
- No LLM call may produce, adjust, or rank an aggregate number.
- A model may only suggest canonical groupings for a human to approve.
- The final numbers always come from deterministic code, never from model judgement.

### 23 — Explicit alias map for skill canonicalisation
- Skill names are canonicalised through an explicit alias map in `config.yaml` that the user owns and can read.
- Never auto-merge skill names by model judgement or string similarity alone.

### 24 — Gap ranking weighted by listing fit score
- A gap in a listing scored 20 is worth far less than a gap in a listing scored 85.
- Never rank gaps by raw frequency alone; always weight by the fit score of the listing the gap came from.

### 25 — Immutable timestamped gap snapshots
- Every gap report run writes a timestamped snapshot. Never overwrite the previous report.
- Trend over time is a first-class output, not an afterthought.

### 26 — Every aggregate number must be traceable
- Any reported gap must be able to list the specific listing IDs it was computed from.
- No number appears in the dashboard that cannot be drilled into.

### 27 — Report sample size alongside every aggregate
- A gap computed from 3 listings and a gap computed from 90 listings must never be presented as equally reliable.
- Sample size is always shown next to the aggregate figure.

## Orchestration

### 28 — State-driven planning, not fixed sequencing
- The Orchestrator reads system state and decides which agents to run.
- It never runs a fixed sequence. Skipping an agent because there is no work for it is a successful outcome, not a failure.

### 29 — Explicit goals and stop conditions per delegation
- Every delegation carries an explicit goal and an explicit stop condition (max items, max duration).
- A sub-agent never decides its own limits — the Orchestrator sets them.

### 30 — The Orchestrator never does an agent's work
- The Orchestrator reads state, delegates, collects results, and logs. Nothing else.
- No fetching, scoring, or analysis logic belongs in the Orchestrator.

### 31 — Plan before execute
- The Orchestrator prints and logs its plan before executing it: which agents will run, which are skipped, and the state value that caused each decision.

### 32 — One agent failing does not stop the cycle
- Log the failure, continue with the remaining plan, and mark the cycle partial.

### 33 — One summary row per cycle
- Every cycle writes exactly one summary row: what ran, what was skipped, why, duration per agent, and the outcome.
