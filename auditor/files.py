"""Reading expected values out of documents with a regular expression.

Purpose: some expected values live in a document (a PDF form, a text file), not
in a database. `extract` reads the file and returns the text a regex captures.
Why this design: a regex over extracted text is deterministic: same file, same
pattern, same answer, no model involved. Paths are `<root>/<relative path>`
where <root> must be a folder named in the AuditConfig, and the resolved file
must stay inside that folder, so an assertion cannot read arbitrary files.
How it can fail: PDF text extraction depends on the PDF having a text layer
(scanned images give nothing); a pattern that does not match raises
FileSourceError (reported as a failed check). Python's `re` has no timeout, so a
pathological pattern could be slow; pattern length is capped.
"""
import re
from pathlib import Path, PurePosixPath

import pdfplumber

from auditor.config import AuditConfig

ALLOWED_SUFFIXES = {".pdf", ".txt", ".csv", ".md"}
MAX_PATTERN_CHARS = 300
MAX_TEXT_CHARS = 200_000


class FileSourceError(Exception):
    pass


def resolve(config: AuditConfig, path_text: str) -> Path:
    rel = PurePosixPath(path_text.replace("\\", "/"))
    if not rel.parts or rel.is_absolute() or ".." in rel.parts:
        raise FileSourceError(f"path must be '<root>/<file>' without '..', got {path_text!r}")
    alias, rest = rel.parts[0], rel.parts[1:]
    if alias not in config.file_roots:
        raise FileSourceError(f"unknown file root '{alias}'; roots: {sorted(config.file_roots)}")
    if not rest:
        raise FileSourceError(f"path {path_text!r} names a root, not a file")
    root = config.file_roots[alias].resolve()
    target = (root / Path(*rest)).resolve()
    if not target.is_relative_to(root):
        raise FileSourceError(f"path {path_text!r} leaves root '{alias}'")
    if target.suffix.lower() not in ALLOWED_SUFFIXES:
        raise FileSourceError(f"unsupported file type {target.suffix!r}; allowed: {sorted(ALLOWED_SUFFIXES)}")
    if not target.is_file():
        raise FileSourceError(f"file not found: {path_text!r}")
    return target


def read_text(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        with pdfplumber.open(path) as pdf:
            text = "\n".join((page.extract_text() or "") for page in pdf.pages)
    else:
        text = path.read_text(encoding="utf-8")
    return text[:MAX_TEXT_CHARS]


def check(config: AuditConfig, path_text: str, pattern: str, group: int = 1) -> str | None:
    """Validator helper: return a problem description, or None if the source is usable."""
    try:
        resolve(config, path_text)
    except FileSourceError as e:
        return str(e)
    if len(pattern) > MAX_PATTERN_CHARS:
        return f"pattern is longer than {MAX_PATTERN_CHARS} characters"
    try:
        compiled = re.compile(pattern, re.MULTILINE)
    except re.error as e:
        return f"pattern is not a valid regular expression: {e}"
    if not 0 <= group <= compiled.groups:
        return f"group {group} does not exist; the pattern has {compiled.groups} group(s)"
    return None


def extract(config: AuditConfig, path_text: str, pattern: str, group: int = 1) -> str:
    problem = check(config, path_text, pattern, group)
    if problem:
        raise FileSourceError(problem)
    text = read_text(resolve(config, path_text))
    match = re.search(pattern, text, re.MULTILINE)
    if not match:
        raise FileSourceError(f"pattern {pattern!r} did not match anything in {path_text!r}")
    return match.group(group).strip()
