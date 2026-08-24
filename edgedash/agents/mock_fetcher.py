"""
mock_fetcher.py — MockFetcher agent.

Returns 12 realistic fake job listings for the configured role and city.
4 of the 12 have stable, hardcoded IDs so duplicate-suppression is
provable on a second run (upsert_listings will return 0 new rows for them).
"""

from __future__ import annotations

from datetime import datetime, timezone

import edgedash.storage as storage
from edgedash.agents.base import Agent, AgentResult
from edgedash.config import Config

# ---------------------------------------------------------------------------
# The 4 stable listings  — same source + url every run, so their SHA-256 id
# never changes.  On run 2 upsert_listings must report 0 new for these.
# ---------------------------------------------------------------------------
_STABLE: list[dict] = [
    {
        "title": "Data Analyst",
        "company": "Flipkart",
        "location": "Bengaluru, Karnataka",
        "url": "https://careers.flipkart.com/jobs/da-001",
        "source": "mock",
        "description": (
            "Own dashboards in Tableau and Power BI for the supply-chain team. "
            "Write complex SQL queries against a Redshift warehouse. "
            "Collaborate with data engineers on dbt models."
        ),
        "posted_at": "2026-07-28T09:00:00+00:00",
    },
    {
        "title": "Senior Data Analyst",
        "company": "Swiggy",
        "location": "Bengaluru, Karnataka",
        "url": "https://careers.swiggy.com/jobs/sda-042",
        "source": "mock",
        "description": (
            "Lead A/B test analysis for the growth team using Python (pandas, scipy). "
            "Build self-serve reporting in Looker. "
            "Strong SQL and statistical analysis required."
        ),
        "posted_at": "2026-07-29T10:30:00+00:00",
    },
    {
        "title": "Business Intelligence Analyst",
        "company": "Razorpay",
        "location": "Bengaluru, Karnataka",
        "url": "https://razorpay.com/jobs/bi-007",
        "source": "mock",
        "description": (
            "Design and maintain BI pipelines from multiple data sources. "
            "Proficiency in SQL, Excel, and at least one BI tool (Power BI preferred). "
            "Experience with Python automation is a plus."
        ),
        "posted_at": "2026-07-30T08:00:00+00:00",
    },
    {
        "title": "Data Analyst — Product",
        "company": "PhonePe",
        "location": "Bengaluru, Karnataka",
        "url": "https://phonepe.com/careers/da-product-19",
        "source": "mock",
        "description": (
            "Partner with product managers to define metrics and build dashboards. "
            "Use SQL, Python, and Metabase daily. "
            "Familiarity with funnel analysis and cohort reporting expected."
        ),
        "posted_at": "2026-07-31T11:00:00+00:00",
    },
]

# ---------------------------------------------------------------------------
# The 8 variable listings — realistic but not deduplicated across runs.
# ---------------------------------------------------------------------------
_VARIABLE: list[dict] = [
    {
        "title": "Junior Data Analyst",
        "company": "Ola",
        "location": "Bengaluru, Karnataka",
        "url": "https://ola.com/careers/jda-2026-08",
        "source": "mock",
        "description": (
            "Entry-level role supporting the ops analytics team. "
            "Excel, basic SQL, and a willingness to learn Python required."
        ),
        "posted_at": "2026-08-01T09:00:00+00:00",
    },
    {
        "title": "Data Analyst — Marketing",
        "company": "Meesho",
        "location": "Bengaluru, Karnataka",
        "url": "https://meesho.com/jobs/da-mkt-55",
        "source": "mock",
        "description": (
            "Analyse campaign performance across channels using SQL and Google Sheets. "
            "Build attribution models and present findings to non-technical stakeholders."
        ),
        "posted_at": "2026-08-01T10:00:00+00:00",
    },
    {
        "title": "Senior Analytics Engineer",
        "company": "CRED",
        "location": "Bengaluru, Karnataka",
        "url": "https://cred.club/jobs/ae-senior-11",
        "source": "mock",
        "description": (
            "Build and maintain dbt models on top of BigQuery. "
            "Strong SQL required; Python and Airflow experience valued. "
            "Work closely with data scientists on feature pipelines."
        ),
        "posted_at": "2026-08-02T08:30:00+00:00",
    },
    {
        "title": "Data Analyst — Finance",
        "company": "Zepto",
        "location": "Bengaluru, Karnataka",
        "url": "https://zepto.com/careers/da-finance-03",
        "source": "mock",
        "description": (
            "Reconciliation, margin analysis, and ad-hoc reporting for the CFO's office. "
            "Advanced Excel and SQL essential; Tableau a plus."
        ),
        "posted_at": "2026-08-02T09:15:00+00:00",
    },
    {
        "title": "Lead Data Analyst",
        "company": "upGrad",
        "location": "Bengaluru, Karnataka",
        "url": "https://upgrad.com/careers/lda-2026-07",
        "source": "mock",
        "description": (
            "Own the learner-analytics function end-to-end. "
            "Python, SQL, and Power BI required. "
            "Manage one junior analyst; present to C-suite monthly."
        ),
        "posted_at": "2026-08-03T07:45:00+00:00",
    },
    {
        "title": "Data Analyst — Operations",
        "company": "Porter",
        "location": "Bengaluru, Karnataka",
        "url": "https://porter.in/careers/da-ops-14",
        "source": "mock",
        "description": (
            "Optimise last-mile delivery using SQL-driven analysis and Python scripts. "
            "Build dashboards in Tableau to track SLA compliance."
        ),
        "posted_at": "2026-08-03T10:00:00+00:00",
    },
    {
        "title": "Analyst — Growth & Retention",
        "company": "Juspay",
        "location": "Bengaluru, Karnataka",
        "url": "https://juspay.in/jobs/analyst-growth-08",
        "source": "mock",
        "description": (
            "Run cohort and retention analyses in Python and SQL. "
            "Own weekly business reviews; comfort with statistical testing required."
        ),
        "posted_at": "2026-08-04T09:00:00+00:00",
    },
    {
        "title": "Data Analyst (Contract)",
        "company": "InMobi",
        "location": "Bengaluru, Karnataka",
        "url": "https://inmobi.com/careers/da-contract-22",
        "source": "mock",
        "description": (
            "Six-month contract to migrate legacy Excel reports to Power BI. "
            "Strong SQL and data-modelling skills required."
        ),
        "posted_at": "2026-08-04T11:30:00+00:00",
    },
]


def _build_rows(config: Config) -> list[dict]:
    """Combine stable and variable listings, stamping fetched_at and ids."""
    now = datetime.now(timezone.utc).isoformat()
    rows: list[dict] = []

    for raw in _STABLE:
        row = dict(raw)
        row["id"] = storage.make_listing_id(row["source"], row["url"])
        row["fetched_at"] = now
        row.setdefault("fit_score", None)
        row.setdefault("fit_reason", None)
        rows.append(row)

    for raw in _VARIABLE:
        row = dict(raw)
        row["id"] = storage.make_listing_id(row["source"], row["url"])
        row["fetched_at"] = now
        row.setdefault("fit_score", None)
        row.setdefault("fit_reason", None)
        rows.append(row)

    return rows


class MockFetcher:
    name: str = "MockFetcher"

    def run(self, config: Config, storage_path: str) -> AgentResult:
        rows = _build_rows(config)
        new_count = storage.upsert_listings(storage_path, rows)
        notes = (
            f"Prepared {len(rows)} listings "
            f"({len(_STABLE)} stable + {len(_VARIABLE)} variable). "
            f"{new_count} were new to the database."
        )
        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=new_count,
            notes=notes,
        )
