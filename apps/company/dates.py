"""Date parsing and display for the mock company.

Purpose: the world stores dates as ISO text (YYYY-MM-DD) but humans type and
read DD/MM/YYYY (Indian convention). The DATE_TRAP fault needs the *same text*
to be read the other way round, so each convention is its own tiny function.
Why this design: no locale magic, no dateutil guessing. A function either
parses exactly its format or raises ValueError.
How it can fail: strptime accepts un-padded days ("3/11/2026"); that is fine
here, but it means "3/11/2026" and "03/11/2026" are the same date.
"""
import re
from datetime import date, datetime

_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def parse_dmy(text: str) -> str:
    """'03/11/2026' -> '2026-11-03' (3 November). Raises ValueError."""
    return datetime.strptime(text.strip(), "%d/%m/%Y").date().isoformat()


def parse_mdy(text: str) -> str:
    """'03/11/2026' -> '2026-03-11' (11 March). Only the DATE_TRAP fault uses this."""
    return datetime.strptime(text.strip(), "%m/%d/%Y").date().isoformat()


def parse_iso(text: str) -> str:
    """Strict 'YYYY-MM-DD' only (date.fromisoformat alone would accept '20261103')."""
    text = text.strip()
    if not _ISO.fullmatch(text):
        raise ValueError(f"not YYYY-MM-DD: {text!r}")
    return date.fromisoformat(text).isoformat()


def to_dmy(iso: str) -> str:
    """'2026-11-03' -> '03/11/2026' for display."""
    return date.fromisoformat(iso).strftime("%d/%m/%Y")
