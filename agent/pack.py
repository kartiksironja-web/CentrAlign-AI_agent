"""The company handbook: loading SOPs (standard operating procedures) from the company pack.

Purpose: each SOP is a markdown file with YAML front matter. The front matter is for code
(`id`, `title`, `summary`, `done_requires`, `allow_literal`); the body is for the model
(rules and a definition of done, never click-paths).
Why this design: the model first sees only id/title/summary of every SOP and picks which to
load, like an employee scanning an index. `done_requires` tells the auditor's validator which
table fields must be verified, so "done" is defined by the company in data, not by the model.
`allow_literal` lists fields whose expected value may be a literal because the rule itself
states it.
How it can fail: front matter is strict (unknown keys, missing id are errors); a SOP with an
empty `done_requires` gives the validator nothing to enforce, which weakens verification.
"""
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError


class PackError(Exception):
    pass


class Sop(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str
    title: str
    summary: str
    done_requires: dict[str, list[str]] = {}
    allow_literal: dict[str, list[str]] = {}
    body: str = ""

    def catalog_line(self) -> str:
        return f"- {self.id}: {self.title}. {self.summary}"


def parse_sop(text: str, source: str = "") -> Sop:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise PackError(f"{source}: a SOP must start with a '---' front matter block")
    end = text.find("\n---", 4)
    if end == -1:
        raise PackError(f"{source}: the front matter block is not closed with '---'")
    try:
        meta = yaml.safe_load(text[4:end]) or {}
        return Sop(**meta, body=text[end + 4:].strip())
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())
        raise PackError(f"{source}: {problems}")
    except (yaml.YAMLError, TypeError) as e:
        raise PackError(f"{source}: front matter is not valid ({str(e).splitlines()[0] if str(e) else type(e).__name__})")


def load_sops(directory: Path) -> list[Sop]:
    sops = [parse_sop(p.read_text(encoding="utf-8"), p.name) for p in sorted(Path(directory).glob("*.md"))]
    ids = [s.id for s in sops]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise PackError(f"duplicate SOP id(s): {dupes}")
    return sorted(sops, key=lambda s: s.id)


def merge_requirements(sops: list[Sop]) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """(done_requires, allow_literal) combined over the selected SOPs, table -> fields."""
    done: dict[str, list[str]] = {}
    literal: dict[str, list[str]] = {}
    for sop in sops:
        for target, source in ((done, sop.done_requires), (literal, sop.allow_literal)):
            for table, fields in source.items():
                merged = target.setdefault(table, [])
                merged.extend(f for f in fields if f not in merged)
    return done, literal
