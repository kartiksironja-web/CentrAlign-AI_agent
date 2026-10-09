"""Turning extracted text into comparable values, and comparing them.

Purpose: a value read from a document is text ("48,200.00", "03/11/2026"); a
value in a database is a number or an ISO date. `coerce` converts the text,
`values_equal` compares the two sides.
Why this design: three small, explicit conversions (text, number, day-first
date) instead of guessing. Numbers compare to within half a unit of the second
decimal, everything else compares as stripped text; a number never equals a
non-numeric string.
How it can fail: a value that does not fit its declared conversion raises
ValueError, which the engine reports as a failed check, never as a pass.
"""
import re
from datetime import datetime

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def coerce(value, as_: str | None):
    """Apply an `as:` conversion ('text', 'number', 'date_dmy') to a value."""
    if as_ is None:
        return value
    text = str(value).strip()
    if as_ == "text":
        return text
    if as_ == "number":
        cleaned = text.replace(",", "").replace(" ", "")
        if not _NUMBER.fullmatch(cleaned):
            raise ValueError(f"not a number: {text!r}")
        return float(cleaned)
    if as_ == "date_dmy":  # '03/11/2026' means 3 November 2026
        try:
            return datetime.strptime(text, "%d/%m/%Y").date().isoformat()
        except ValueError:
            raise ValueError(f"not a DD/MM/YYYY date: {text!r}")
    raise ValueError(f"unknown 'as' conversion: {as_!r}")


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_float(value) -> float | None:
    if _is_number(value):
        return float(value)
    if isinstance(value, str) and _NUMBER.fullmatch(value.strip()):
        return float(value.strip())
    return None


def values_equal(actual, expected) -> bool:
    if actual is None or expected is None:
        return actual is None and expected is None
    if _is_number(actual) or _is_number(expected):
        a, e = _to_float(actual), _to_float(expected)
        return a is not None and e is not None and abs(a - e) < 0.005
    return str(actual).strip() == str(expected).strip()
