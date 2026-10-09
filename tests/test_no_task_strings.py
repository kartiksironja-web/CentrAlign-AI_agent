"""Generalization rule, enforced by a test instead of a promise.

agent/ and auditor/ must contain no task-specific or company-specific words.
All company knowledge lives in company_pack/ (data) and apps/company/ (the mock
world). If this test fails, a task detail leaked into the generic runtime.
(agent/ does not exist yet; it is checked automatically once it does.)
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERIC_PACKAGES = ["auditor", "agent"]
TEXT_SUFFIXES = {".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml"}
# Matched as whole words (letters on either side do not count), so "interpret"
# is fine but "erp_bills" and "Invoice" are caught.
BANNED = [
    r"invoices?", r"bills?", r"vendors?", r"acme", r"zenith", r"globex", r"kaveri",
    r"erp", r"portal", r"gst", r"inr", r"rupees?", r"nimbus", r"payments?",
]
PATTERN = re.compile(r"(?<![a-z])(" + "|".join(BANNED) + r")(?![a-z])", re.IGNORECASE)


def offenders(package: str) -> list[str]:
    found = []
    for path in sorted((ROOT / package).rglob("*")):
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES or "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for match in PATTERN.finditer(line):
                found.append(f"{path.relative_to(ROOT)}:{number}: {match.group(0)!r}")
    return found


def test_the_auditor_package_exists_and_is_scanned():
    assert (ROOT / "auditor").is_dir()
    assert any((ROOT / "auditor").glob("*.py"))


def test_generic_packages_contain_no_task_or_company_words():
    problems = [p for pkg in GENERIC_PACKAGES if (ROOT / pkg).is_dir() for p in offenders(pkg)]
    assert problems == [], "task-specific words found in generic code:\n" + "\n".join(problems)


def test_the_scanner_itself_catches_what_it_should():
    assert PATTERN.search("SELECT * FROM erp_bills")
    assert PATTERN.search("the Invoice total")
    assert PATTERN.search("Acme Supplies")
    assert not PATTERN.search("interpret the property")
