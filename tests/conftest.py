"""
conftest.py — test isolation.

The test suite must never touch the hosted Postgres. storage.py reads
DATABASE_URL at import time, so this file blanks it before pytest collects
any test module. An empty string is treated as "set" by python-dotenv's
default override=False, so a repo .env cannot put it back.
"""

import os

os.environ["DATABASE_URL"] = ""
