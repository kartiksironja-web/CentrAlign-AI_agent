"""A tiny, generic world for auditor tests (no company names on purpose).

main.db   owners(id pk, name), items(id pk autoincrement, owner_id, label, amount, due),
          notes(text)   <- no primary key, to test rowid keying
src.db    source_items(code pk, owner_id, listed_on, amount, due)  <- the "source system"
docs/     form.txt and doc.pdf holding "Owner Name / Amount / Due" lines
Newest source item of owner 1 is S2 (listed 2026-03-01, amount 750.0, due 2026-04-02).
"""
import sqlite3

import pytest
from fpdf import FPDF

from auditor.assertions import parse_assertions_yaml
from auditor.config import AuditConfig, DbSpec
from auditor.engine import run_audit
from auditor.readonly import ReadOnlyDbs
from auditor.snapshot import take_snapshot
from auditor.validate import Requirements

FORM_LINES = ["Owner Name: Zed Corp", "Amount: 1,250.50", "Due: 03/11/2026"]


class World:
    def __init__(self, root):
        self.root = root
        self.main_path = root / "main.db"
        self.src_path = root / "src.db"
        self.docs = root / "docs"
        self.docs.mkdir()
        self._build()
        self.config = AuditConfig(
            databases={
                "main": DbSpec(self.main_path, ("owners", "items", "notes")),
                "src": DbSpec(self.src_path, ("source_items",)),
            },
            file_roots={"docs": self.docs},
        )

    def _build(self):
        main = sqlite3.connect(self.main_path)
        main.executescript(
            """
            CREATE TABLE owners (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, label TEXT, amount REAL, due TEXT);
            CREATE TABLE notes (text TEXT);
            INSERT INTO owners VALUES (1, 'Alpha'), (2, 'Beta');
            INSERT INTO items (owner_id, label, amount, due) VALUES (1, 'first', 100.0, '2026-01-05');
            INSERT INTO notes VALUES ('hello');
            """
        )
        main.commit()
        main.close()
        src = sqlite3.connect(self.src_path)
        src.executescript(
            """
            CREATE TABLE source_items (code TEXT PRIMARY KEY, owner_id INTEGER, listed_on TEXT, amount REAL, due TEXT);
            INSERT INTO source_items VALUES
              ('S1', 1, '2026-02-01', 500.0, '2026-03-03'),
              ('S2', 1, '2026-03-01', 750.0, '2026-04-02'),
              ('S3', 1, '2026-01-10', 90.0,  '2026-02-09'),
              ('S4', 2, '2026-03-05', 10.0,  '2026-04-04');
            """
        )
        src.commit()
        src.close()
        (self.docs / "form.txt").write_text("\n".join(FORM_LINES), encoding="utf-8")
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        for line in FORM_LINES:
            pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
        pdf.output(str(self.docs / "doc.pdf"))

    def write(self, db: str, sql: str, params=()):
        """Change a database the way the agent's actions would (tests may write)."""
        conn = sqlite3.connect(self.main_path if db == "main" else self.src_path)
        conn.execute(sql, params)
        conn.commit()
        conn.close()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture
def dbs(world):
    opened = ReadOnlyDbs(world.config)
    yield opened
    opened.close()


@pytest.fixture
def before(dbs):
    return take_snapshot(dbs)


@pytest.fixture
def audit(dbs, before):
    """audit(yaml_text, requirements=Requirements()) -> AuditResult, against the CURRENT state."""

    def _audit(yaml_text: str, requirements: Requirements = Requirements()):
        return run_audit(dbs, parse_assertions_yaml(yaml_text), requirements, before)

    return _audit
