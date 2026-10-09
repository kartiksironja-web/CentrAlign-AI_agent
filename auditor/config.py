"""Where the auditor may look: databases (by label) and document folders.

Purpose: AuditConfig names the databases the auditor may open, which tables each
label exposes, and which folders documents may be read from.
Why this design: this is passed in, never hard-coded. `load_config` reads it
from a YAML file in the company pack, so a different company is a different file.
How it can fail: a label that lists a table the file does not contain is a
configuration error and is reported loudly when the databases are opened.
"""
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class DbSpec:
    path: Path
    tables: tuple[str, ...] | None = None  # None means every user table in the file


@dataclass(frozen=True)
class AuditConfig:
    databases: dict[str, DbSpec]
    file_roots: dict[str, Path] = field(default_factory=dict)


def load_config(yaml_path: Path, bases: dict[str, Path]) -> AuditConfig:
    """Build an AuditConfig from a YAML file.

    `bases` maps the base names used in the file (for example "data", "repo")
    to real folders chosen by the runner for this run.
    """
    raw = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8")) or {}
    databases = {
        label: DbSpec(
            path=bases[spec["base"]] / spec["file"],
            tables=tuple(spec["tables"]) if spec.get("tables") else None,
        )
        for label, spec in (raw.get("databases") or {}).items()
    }
    file_roots = {
        alias: bases[spec["base"]] / spec["path"] for alias, spec in (raw.get("file_roots") or {}).items()
    }
    return AuditConfig(databases=databases, file_roots=file_roots)
