"""Value conversion/comparison and document reading."""
import pytest

from auditor import files
from auditor.values import coerce, values_equal


# ---- values --------------------------------------------------------------

def test_coerce():
    assert coerce("1,250.50", "number") == 1250.5
    assert coerce(" 48 200 ", "number") == 48200.0
    assert coerce("03/11/2026", "date_dmy") == "2026-11-03"
    assert coerce("  hi ", "text") == "hi"
    assert coerce("x", None) == "x"


def test_coerce_rejects_values_that_do_not_fit():
    for text, kind in (("abc", "number"), ("12.5.6", "number"), ("2026-11-03", "date_dmy"), ("31/02/2026", "date_dmy")):
        with pytest.raises(ValueError):
            coerce(text, kind)
    with pytest.raises(ValueError, match="unknown"):
        coerce("x", "wibble")


def test_values_equal():
    assert values_equal(750, 750.0)
    assert values_equal(1250.5, 1250.504) and not values_equal(1250.5, 1250.51)
    assert values_equal("750", 750.0) and values_equal(750.0, "750.00")
    assert not values_equal("abc", 5) and not values_equal(5, "abc")
    assert values_equal(" Zed ", "Zed") and not values_equal("zed", "Zed")
    assert values_equal(None, None) and not values_equal(None, "") and not values_equal(0, None)
    assert not values_equal(True, 1)  # a bool is not a number here


# ---- files ---------------------------------------------------------------

def test_extract_from_text_and_pdf(world):
    for name in ("docs/form.txt", "docs/doc.pdf"):
        assert files.extract(world.config, name, r"Owner Name: (.+)") == "Zed Corp"
        assert files.extract(world.config, name, r"Amount: ([\d,\.]+)") == "1,250.50"
    assert files.extract(world.config, "docs/form.txt", r"Due: \d{2}/\d{2}/\d{4}", group=0) == "Due: 03/11/2026"


@pytest.mark.parametrize("bad", [
    "docs/../main.db", "../main.db", "/etc/passwd", "C:/Windows/win.ini", "docs\\..\\main.db",
    "nowhere/form.txt", "docs", "docs/", "docs/missing.txt", "docs/form.exe", "",
])
def test_paths_that_leave_the_roots_or_do_not_exist_are_rejected(world, bad):
    with pytest.raises(files.FileSourceError):
        files.resolve(world.config, bad)


def test_unsupported_suffix_and_a_database_file_cannot_be_read(world):
    (world.docs / "copy.db").write_bytes(b"x")
    with pytest.raises(files.FileSourceError, match="unsupported"):
        files.resolve(world.config, "docs/copy.db")


def test_pattern_problems(world):
    cfg = world.config
    assert "did not match" in str(pytest.raises(files.FileSourceError, files.extract, cfg, "docs/form.txt", r"Nothing: (.+)").value)
    assert files.check(cfg, "docs/form.txt", r"(unclosed") is not None
    assert "group 2" in files.check(cfg, "docs/form.txt", r"Owner Name: (.+)", 2)
    assert "longer than" in files.check(cfg, "docs/form.txt", "a" * 301, 0)
    assert files.check(cfg, "docs/form.txt", r"Owner Name: (.+)", 1) is None
