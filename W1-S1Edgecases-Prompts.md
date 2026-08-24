# Class 1 Prompts — "Signal or Noise?"
### Week 1 · Session 1.1 · EdgeDash · Proof of Ship, August Cohort

These are every prompt used in Class 1, in order. Paste them into **Kiro** one at a time,
in sequence — each one builds on the last.

## Before you start

1. Create an empty folder called `edgedash` and open it in Kiro.
2. Have a terminal open inside that folder.
3. Python 3.11 or newer. Check with `python --version`.
4. **No API keys needed for this class.** Nothing to pay for, nothing to sign up for.

## Three rules for using these prompts

**1. Read every diff before you accept it.** You are the senior engineer here; Kiro is a
very fast junior who never asks questions. If you can't explain a line, don't keep it —
ask Kiro to explain it, or regenerate that module.

**2. One prompt at a time.** Don't paste two together to save time. Each prompt ends at a
point where you should stop and look at what happened.

**3. If a prompt gives you something wrong, don't hand-patch it silently.** Say what's
wrong in a follow-up message and let it fix the module. There are suggested follow-ups at
the bottom of this file.

---

## PROMPT C1-P1 — Project steering file

**What it does:** Writes your project's constitution. Kiro loads steering files into every
interaction, so these rules apply to all future prompts without you repeating them.

**Run it first. Nothing else works properly without it.**

```text
Create a Kiro steering file at .kiro/steering/edgedash.md that will apply to
every interaction in this project. It must capture these rules:

PROJECT: EdgeDash — an autonomous AI career intelligence agent. A scheduled loop
that fetches live job listings, scores them for fit against my profile, surfaces
my skill gaps, verifies its own output, and publishes a Streamlit dashboard.

ARCHITECTURE (do not deviate without telling me):
Trigger (scheduled) -> Orchestrator -> sub-agents (Fetcher, Scorer, GapAnalyzer)
-> Verifier -> Storage -> Dashboard (read-only).
The Orchestrator reads state and delegates; it never fetches or scores directly.
Each sub-agent has one goal and one stop condition.

HARD RULES:
1. Python 3.11+. Standard library first. Add a dependency only when it genuinely
   saves real work, and tell me why before you add it.
2. ALL storage access goes through a single storage module with a thin interface.
   No other module may import sqlite3 directly. We will swap SQLite for hosted
   Postgres in week 4 and it must be a one-file change.
3. Never hardcode my role, city, keywords, or skills profile. Everything
   user-specific lives in config.
4. No secrets in code. Environment variables only, loaded in one place.
5. Every agent run writes a row to a cycle_log table: what ran, when, how many
   records touched, pass/fail, and any retry reason.
6. Fail loudly. No bare `except: pass`. If something is wrong I want to see it.
7. Type hints on every function signature. Docstrings only where the intent is
   not obvious from the name.
8. Keep files under ~150 lines. Split before that becomes a problem.

STYLE: Small, testable functions. Plain readable Python over clever Python.
When I ask for one module, build one module — do not scaffold the whole app.
```

**Check before moving on:** the file exists at `.kiro/steering/edgedash.md`, and rule 2
(single storage module) is in it. That rule prevents the most common way this project
breaks in week 4.

---

## PROMPT C1-P2 — Config + storage layer

**What it does:** Builds the two foundation modules — your config file, and the only module
in the project allowed to talk to the database.

```text
Build two modules only. No agents yet, no loop yet.

1. edgedash/config.py
   - A dataclass `Config` holding: target_role (str), target_city (str),
     keywords (list[str]), my_skills (list[str]), experience_years (int),
     db_path (str), min_fit_score (int).
   - Load it from a `config.yaml` at the repo root, with sensible defaults if a
     field is missing. Fail with a clear error if config.yaml is absent.
   - Also write an example `config.yaml` filled in for a "Data Analyst" in
     "Bengaluru" so I can see the shape and edit it.

2. edgedash/storage.py
   - The ONLY module allowed to touch sqlite3.
   - `init_db(path)` creating three tables if absent:
       listings(id TEXT PRIMARY KEY, title, company, location, url,
                description, source, posted_at, fetched_at,
                fit_score INTEGER NULL, fit_reason TEXT NULL)
       skill_gaps(skill TEXT PRIMARY KEY, frequency INTEGER, last_seen TEXT)
       cycle_log(id INTEGER PRIMARY KEY AUTOINCREMENT, agent TEXT, started_at,
                 finished_at, records_touched INTEGER, status TEXT, notes TEXT)
   - Functions: `upsert_listings(rows) -> int` returning the count of genuinely
     NEW rows inserted (this is what makes dedup observable);
     `count_unscored()`; `last_fetch_time()`; `log_cycle(...)`;
     `get_listings(limit, min_score)`.
   - Use `INSERT OR IGNORE` on the primary key for dedup. The listing `id` must
     be a stable hash of source + url so the same job is never counted twice.

Type hints throughout. Show me the files, then tell me in one line how to
initialise the database.
```

**Check before moving on:** `upsert_listings` returns the count of **new** rows, not total
rows — that's what lets you see dedup working. And the listing `id` is a hash of source +
URL, not a random value or a row number.

---

## PROMPT C1-P3 — Orchestrator skeleton + mock Fetcher

**What it does:** Builds the loop itself, with a fake Fetcher so you can prove the pipe
works before Thursday's real scraper goes in.

```text
Now the loop skeleton. Still no real network calls.

1. edgedash/agents/base.py
   - An `Agent` protocol/ABC: every agent exposes `name: str` and
     `run(config, storage) -> AgentResult`.
   - `AgentResult` dataclass: agent, status ("ok"|"failed"), records_touched,
     notes.

2. edgedash/agents/mock_fetcher.py
   - A `MockFetcher` implementing Agent. Returns 12 realistic fake job listings
     for the configured role and city — varied companies, varied seniority,
     realistic short descriptions with real skill names in them.
   - IMPORTANT: 4 of the 12 must be identical across every run (same stable id)
     so I can prove deduplication works on the second run.
   - It writes via storage.upsert_listings and returns the NEW count.

3. edgedash/orchestrator.py
   - `run_cycle(config)`:
     a. init the db
     b. read state: last_fetch_time, count_unscored
     c. print a short plan of what it has decided to do and WHY
     d. run the agents it decided to run
     e. log every agent run to cycle_log
     f. print a cycle summary table
   - Registry pattern for agents so Thursday's real Fetcher drops in by swapping
     one line. Scorer and GapAnalyzer: register them as clearly-marked
     placeholders that log "not implemented yet" and skip.

4. run_cycle.py at the repo root — the entry point. `python run_cycle.py`
   runs one full cycle.

Console output should be genuinely readable — I am going to run this on camera.
Show the state it read, the plan it chose, each agent's result, and the summary.
```

**Now run it, twice:**

```bash
python run_cycle.py
python run_cycle.py
```

**This is the moment of the class.** First run: 12 listings, 12 new. Second run: 12
listings, **4 new** — 8 were deduplicated. If your second run says 12 new, your dedup is
broken; go back to `storage.py` and check that the `id` is a stable hash and that you're
using `INSERT OR IGNORE`.

---

## PROMPT C1-P4 — README with architecture (optional, do it on your own)

**What it does:** Generates the README for your repo. Not covered on camera, but graders
read READMEs first, and you need this for your submission anyway.

```text
Write a README.md for this repo.

Include:
- One-paragraph description of EdgeDash: an autonomous career intelligence loop
  that fetches, scores, and gap-analyses job listings daily and publishes a
  dashboard.
- An ASCII architecture diagram showing:
  Trigger -> Orchestrator -> [Fetcher | Scorer | GapAnalyzer] -> Verifier
  -> Storage -> Dashboard (read-only)
- A "Current status" section with a checklist of what is built vs what is coming
  in weeks 2, 3 and 4. Mark the mock Fetcher clearly as temporary.
- Setup: Python version, install steps, how to edit config.yaml, how to run
  `python run_cycle.py`.
- A "Design decisions" section explaining, in 2-3 lines each: why storage is
  isolated behind one module, why listing ids are stable hashes, and why the
  Orchestrator delegates instead of doing the work itself.

Keep it tight. No filler, no badges, no emoji.
```

---

## Your assignment

**Tonight (from this class):**
- [ ] Repo initialised, pushed to GitHub
- [ ] `.kiro/steering/edgedash.md` in place
- [ ] `python run_cycle.py` runs clean, twice, with dedup visible on the second run
- [ ] `config.yaml` filled in with **your** target role, **your** city, **your** real skills

**By Sunday (after Thursday's class):**
- [ ] Real Fetcher replacing the mock
- [ ] **50+ genuine live listings** in your database — real data, not test data
- [ ] 2–3 minute walkthrough video: agent running, database growing
- [ ] Submit GitHub repo link + video

**Badge unlocked:** The Tracker

**Also, in the next hour:** post your architecture diagram on LinkedIn with `#MyEdge` and
say what you're building. Post the plan, not the finished thing — it commits you.

---

## Fixing common problems

Paste these as follow-ups if something goes wrong. Describe the actual symptom rather than
guessing at the cause.

**Second run shows all 12 as new (dedup broken):**
```text
Deduplication isn't working — running run_cycle.py twice reports 12 new listings
both times, when the second run should report 4. Show me how the listing id is
generated and how upsert_listings inserts. The id must be a stable hash of
source + url, and the insert must be INSERT OR IGNORE on that primary key. Fix
whichever part is wrong and explain what was broken.
```

**Another module is importing sqlite3 directly (violates rule 2):**
```text
Check every file for direct sqlite3 imports outside edgedash/storage.py. Steering
rule 2 says storage access goes through the storage module only. Move any stray
database code into storage.py behind a named function and update the callers.
```

**Kiro built more than you asked for:**
```text
That's more than I asked for. I only wanted <the module you asked for>. Remove
everything else you created in this step — I want to build and review one module
at a time.
```

**Console output is hard to read on camera:**
```text
Rewrite only the console output in orchestrator.py. I need to read it on a screen
recording. Show clearly: the state it read, the plan it chose and why, one line
per agent with its result, and a final summary with total listings and new
listings this cycle. Use plain aligned text, no external formatting libraries.
```

**Config isn't loading / you want to check it independently:**
```text
Add a `python -m edgedash.config` entry point that loads config.yaml, prints
every resolved field, and exits non-zero with a clear message if anything is
missing or malformed. I want to verify my config without running a full cycle.
```
