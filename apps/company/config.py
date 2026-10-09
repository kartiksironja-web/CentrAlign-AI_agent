"""Where things live on disk, and loading .env.

Purpose: one place that answers "which folder is the company data in?".
Why this design: the DB and the portal PDFs live together in ONE data folder
(COMPANY_DATA_DIR). The eval runner can then give every run a fresh copy of
that folder, and the app never needs to know about the repo layout.
How it can fail: a relative COMPANY_DATA_DIR is resolved against the current
working directory, so run commands from the repo root.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
INPUTS_DIR = REPO_ROOT / "inputs"


def load_env() -> None:
    """Load REPO_ROOT/.env into os.environ without overriding real env vars."""
    load_dotenv(REPO_ROOT / ".env", override=False)


def default_data_dir() -> Path:
    return Path(os.environ.get("COMPANY_DATA_DIR") or REPO_ROOT / "data")
