# EdgeDash — Autonomous AI Career Intelligence Agent

EdgeDash is a scheduled agentic loop that fetches live job listings, scores them for fit against your profile, surfaces skill gaps, and gives you a terminal report every morning — so you know exactly which skills to learn next and why.

---

## What it does

Every time you run a cycle:

1. **Fetcher** — pulls live listings from job boards, filters by your keywords and city
2. **Scorer** — uses an LLM to extract structured facts from each description, then scores fit with deterministic Python (no model scores, no black-box numbers)
3. **GapAnalyzer** — computes which skills are blocking your highest-scoring opportunities, weighted by listing quality
4. **Snapshot** — writes a timestamped gap report to SQLite so you can track trends over time

---

## Architecture

```
Trigger (scheduled or manual)
  └─> Orchestrator
        ├─> Fetcher        (retrieves raw job listings)
        ├─> Scorer         (extracts facts + scores fit)
        └─> GapAnalyzer    (ranks skill gaps by opportunity cost)
              └─> Storage (SQLite — single module, swappable)
```

- The model **extracts facts only** — it never scores, ranks, or evaluates fit
- All arithmetic is deterministic Python in one function
- Every run is logged; every number is traceable to the listings that produced it

---

## Quick start

```bash
# 1. Clone and create a virtual environment
git clone https://github.com/Piro-Programmer/edgedash.git
cd edgedash
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Set your API key
cp .env.example .env
# Edit .env and add your GEMINI_API_KEY

# 4. Edit config.yaml — set your role, city, skills
# (see Configuration section below)

# 5. Run a cycle
python run_cycle.py
```

---

## Daily commands

```bash
# Run a full fetch → score → gap-analysis cycle
python run_cycle.py

# View the latest skill gap report
python -m edgedash.gaps

# View the trend since your first run
python -m edgedash.gaps --trend

# Audit raw skill strings in the database (find aliases to add)
python -m edgedash.skills --audit

# Verify the LLM connection is working
python -m edgedash.llm --check
```

---

## Configuration

Everything user-specific lives in `config.yaml`. No values are hardcoded.

```yaml
target_role: "Security Engineer"
target_city: "Berlin"

keywords:
  - "security"
  - "python"

my_skills:
  - "python"
  - "sql"
  - "security"

experience_years: 3

llm_provider: "gemini"      # "gemini" or "ollama" (local, no key needed)
llm_model: "gemini-3.5-flash"
llm_batch_size: 25          # listings scored per cycle — controls LLM cost

# Scoring weights (must sum to 1.0)
weight_skill_match:   0.45
weight_seniority_fit: 0.25
weight_location_fit:  0.15
weight_recency:       0.15

# Skill aliases — yours to edit as you see your data
skill_aliases:
  "k8s":        "kubernetes"
  "postgresql": "postgres"
  "ml":         "machine learning"
```

To go **offline** (no network, no API key needed):
```yaml
use_mock_fetcher: true
```

---

## Gap report example

```
╔══════════════════════════════════════════════════════════════════╗
║  EDGEDASH — SKILL GAP REPORT                                     ║
║  Snapshot : 2026-08-22 12:53  (sample: 27 gap-exposures)         ║
╚══════════════════════════════════════════════════════════════════╝

  #   skill                 blocked    cost   mean   top   n+  opportunity
  ─────────────────────────────────────────────────────────────────────────
    1  kubernetes                  4     1.6   39.0    39    1  ████████████████████
    2  gcp                         3     1.2   40.0    42    0  ███████████████░░░░░
    3  postgres                    3     1.2   40.0    42    0  ███████████████░░░░░
    4  typescript                  3     1.0   34.0    39    1  █████████████░░░░░░░
```

- **blocked** — how many listings require this skill and you don't have it
- **cost** — `sum(score / 100)` for those listings — weighted by listing quality
- **⚠** — fewer than 3 listings, treat as low confidence

---

## Scoring design

The model is never asked for a number. It reads the job description and returns structured facts:

```json
{
  "required_skills": ["python", "kubernetes", "terraform"],
  "nice_to_have":    ["go", "rust"],
  "seniority":       "senior",
  "years_required":  5,
  "remote_ok":       true
}
```

Four components then compute the score in pure Python:

| Component | Weight | Logic |
|---|---|---|
| `skill_match` | 0.45 | fraction of required skills you have; nice-to-have at ⅓ weight |
| `seniority_fit` | 0.25 | band distance: exact=1.0, one off=0.6, two=0.25, three+=0.0 |
| `location_fit` | 0.15 | remote=1.0, city match=1.0, unknown=0.5, elsewhere=0.1 |
| `recency` | 0.15 | linear decay from 1.0 today to 0.0 at 30 days |

---

## Project structure

```
edgedash/
  agents/
    fetcher.py        # fetches listings from registered sources
    scorer.py         # orchestrates extraction + scoring per listing
    extractor.py      # LLM extraction step (only file that calls the model)
    gap_analyzer.py   # deterministic gap computation, no LLM
    mock_fetcher.py   # offline development, no network
  sources/
    base.py           # Source protocol + SOURCES registry
    arbeitnow.py      # Arbeitnow job board source
    http.py           # shared HTTP helper (timeout, retry, User-Agent)
  config.py           # Config dataclass + load_config()
  llm.py              # single LLM gateway — complete_json()
  scoring.py          # deterministic score_listing() and build_reason()
  skills.py           # canonical() + --audit CLI
  gaps.py             # gap report CLI + trend report
  storage.py          # all DB access — one file, swappable backend
  orchestrator.py     # cycle runner
config.yaml           # your profile and all tunable parameters
run_cycle.py          # entry point
tests/
  test_scoring.py
  test_skills.py
```

---

## LLM providers

| Provider | Key required | How to set |
|---|---|---|
| `gemini` | Yes — `GEMINI_API_KEY` | Add to `.env` (see `.env.example`) |
| `ollama` | No | Run Ollama locally, set `llm_provider: "ollama"` in `config.yaml` |

Switching providers is a one-line change in `config.yaml`. No code changes needed.

---

## Running tests

```bash
pytest tests/ -v
```

`tests/conftest.py` blanks `DATABASE_URL` so the suite always runs against a
temporary SQLite file. Never remove it — without it a local `.env` points the
tests at the hosted Postgres and they will write to production.

---

## Deployment

EdgeDash runs as **two separate pieces**. This split is the whole design:

| Piece | Where | Role |
|---|---|---|
| `app.py` | Streamlit Community Cloud | **Reader.** Renders what is in the database. Never fetches, scores, or writes. |
| `run_cycle.py` | GitHub Actions (`.github/workflows/cycle.yml`) | **Writer.** Runs the agent loop every 6 hours and writes to Postgres. |

Both point at the same hosted Postgres. Deploying only the Streamlit app gives
you a permanently empty dashboard, because nothing is writing to the database.

### 1. Provision Postgres

Any hosted Postgres works (Neon, Supabase, Railway). Copy its connection URI.

### 2. Configure the Streamlit app

In **Manage app → Settings → Secrets**, add:

```toml
DATABASE_URL = "postgresql://user:password@host/dbname?sslmode=require"
GEMINI_API_KEY = "your-key"
```

Streamlit Cloud exposes secrets as environment variables, which is how
`storage.py` and `llm.py` pick them up. `GEMINI_API_KEY` is needed only by the
"Ask your data" panel.

### 3. Configure the scheduler

In **GitHub → Settings → Secrets and variables → Actions**, add repository
secrets `DATABASE_URL` (same URI) and `GEMINI_API_KEY`.

Then run the workflow once by hand to populate the database:
**Actions → EdgeDash cycle → Run workflow**. After that it runs every 6 hours.

The workflow fails loudly if either secret is missing, and `run_cycle.py` exits
non-zero on a `degraded` outcome so a broken pipeline shows up as a red run
rather than a silent green one.

### Creating the schema

`app.py` calls `storage.init_db()` on startup, and the workflow runs
`python -m edgedash.storage --migrate` before each cycle. Both are
`CREATE TABLE IF NOT EXISTS` only. To inspect a database:

```bash
python -m edgedash.storage --check
```

---

## Requirements

- Python 3.11+
- `google-genai` (Gemini) or a local Ollama server
- Free Gemini API key — [get one at ai.google.dev](https://ai.google.dev)
