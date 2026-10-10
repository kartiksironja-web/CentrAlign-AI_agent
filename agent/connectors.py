"""Connector manifests: how the agent and the gate learn about a company system.

Purpose: one YAML file per system, loaded into typed objects. The AGENT reads the
description, capabilities and record names (`Connector.describe()`); the GATE reads
the base URL (which hosts exist) and `actions` (which raw requests are which named
action, how to read their fields, which fields identify "the same action" for approvals, which
fields a request may leave out, and which field names a record that could be confused with another).
Why this design: all company knowledge stays in data files. Field expressions such as
"form.amount|json.amount" mean "first source that has it"; sources are form, json, path
(from {name} in the path template) and query. A `lookup` lets the gate fetch a field the
request itself does not carry, by a GET to the same system.
How it can fail: manifests are strict (unknown keys, bad expressions, duplicate action
names are load errors), because a silently ignored typo in a permission file is worse
than a crash. `describe()` never includes the actions section: that is gate internals.
"""
import re
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_EXPR_ALT = re.compile(r"(form|json|path|query)\.([A-Za-z_]\w*)")


class ConnectorError(Exception):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def parse_expression(text: str) -> tuple[tuple[str, str], ...]:
    """'form.amount|json.amount' -> (('form','amount'), ('json','amount'))."""
    alts = []
    for part in text.split("|"):
        m = _EXPR_ALT.fullmatch(part.strip())
        if not m:
            raise ValueError(f"bad field expression {part.strip()!r}; use form.x, json.x, path.x or query.x")
        alts.append((m.group(1), m.group(2)))
    return tuple(alts)


@lru_cache(maxsize=None)
def _compile_template(template: str) -> re.Pattern:
    """'/things/{id}/done' -> regex with a named group per {name} (one path segment)."""
    out, pos = "", 0
    for m in re.finditer(r"\{(\w+)\}", template):
        out += re.escape(template[pos:m.start()]) + f"(?P<{m.group(1)}>[^/]+)"
        pos = m.end()
    return re.compile("^" + out + re.escape(template[pos:]) + "$")


class MatchSpec(_Strict):
    method: str
    path: str
    requires_any: tuple[str, ...] = ()  # the request must carry at least one of these fields

    @field_validator("method")
    @classmethod
    def _upper(cls, v):
        return v.upper()

    @field_validator("path")
    @classmethod
    def _absolute(cls, v):
        if not v.startswith("/"):
            raise ValueError("path must start with '/'")
        return v

    @field_validator("requires_any")
    @classmethod
    def _exprs(cls, v):
        for expr in v:
            parse_expression(expr)
        return v

    def path_params(self, path: str) -> dict[str, str] | None:
        m = _compile_template(self.path).match(path)
        return m.groupdict() if m else None


class Lookup(_Strict):
    url: str  # relative to base_url; may contain {path.name}
    extract: dict[str, str]

    @field_validator("extract")
    @classmethod
    def _json_only(cls, v):
        for expr in v.values():
            if any(source != "json" for source, _ in parse_expression(expr)):
                raise ValueError("a lookup can only read json.<field> from the response")
        return v


class Entity(_Strict):
    """Which extracted field names a record, and where to list the records it could be confused with.

    Before the gate lets the write through it GETs `candidates_url` (a JSON list of records), finds the
    record the request points at (`id_field` equal to the extracted field) and looks for OTHER records whose
    `name_field` is similar (see agent/similar.py). If there are any, a human must be asked first."""
    field: str  # a key of the action's `extract`
    candidates_url: str  # relative to base_url
    id_field: str
    name_field: str
    ignore_words: tuple[str, ...] = ()  # words that say nothing about identity, e.g. legal suffixes (company knowledge)
    min_shared: float = 0.5  # share of the shorter name's words the other name must contain

    @field_validator("min_shared")
    @classmethod
    def _fraction(cls, v):
        if not 0 < v <= 1:
            raise ValueError("min_shared must be above 0 and at most 1")
        return v


class Action(_Strict):
    name: str
    match: tuple[MatchSpec, ...]
    extract: dict[str, str] = Field(default_factory=dict)
    approval_key: tuple[str, ...] = ()
    lookup: Lookup | None = None
    optional_fields: tuple[str, ...] = ()  # fields a request may legitimately leave out; any other missing field fails closed
    entity: Entity | None = None

    @field_validator("extract")
    @classmethod
    def _exprs(cls, v):
        for expr in v.values():
            parse_expression(expr)
        return v

    @model_validator(mode="after")
    def _names_exist(self):
        known = set(self.extract) | (set(self.lookup.extract) if self.lookup else set())
        unknown = sorted(set(self.optional_fields) - known)
        if unknown:
            raise ValueError(f"optional_fields {unknown} are not extracted by action {self.name!r}")
        if self.entity is not None and self.entity.field not in self.extract:
            raise ValueError(f"entity field {self.entity.field!r} is not extracted by action {self.name!r}")
        return self


class Record(_Strict):
    db: str
    table: str
    description: str
    fields: dict[str, str]


class Connector(_Strict):
    id: str
    name: str
    base_url: str
    description: str
    capabilities: dict[str, list[str]] = Field(default_factory=dict)
    records: tuple[Record, ...] = ()
    actions: tuple[Action, ...] = ()

    @field_validator("base_url")
    @classmethod
    def _http(cls, v):
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("base_url must be an http(s) URL with a host")
        return v.rstrip("/")

    @property
    def origin(self) -> tuple[str, str, int]:
        return origin_of(self.base_url)

    def describe(self) -> str:
        """The text the agent sees. Deliberately leaves out `actions`."""
        lines = [f"{self.name} (id: {self.id})", f"Base URL: {self.base_url}", self.description.strip()]
        for kind, items in self.capabilities.items():
            if items:
                label = {"ui": "UI capabilities", "api": "API capabilities"}.get(kind, kind.replace("_", " ").capitalize())
                lines.append(f"{label}:")
                lines += [f"  - {item}" for item in items]
        if self.records:
            lines.append("Records kept (names to use in declare_assertions):")
            for r in self.records:
                lines.append(f"  - db '{r.db}', table '{r.table}': {r.description}")
                lines += [f"      {name}: {meaning}" for name, meaning in r.fields.items()]
        return "\n".join(lines)


def origin_of(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, (parts.hostname or "").lower(), port


def parse_connector(data, base_url: str | None = None) -> Connector:
    try:
        connector = Connector.model_validate(data)
    except ValidationError as e:
        lines = [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        raise ConnectorError("invalid connector: " + " | ".join(lines))
    return connector.model_copy(update={"base_url": base_url.rstrip("/")}) if base_url else connector


def load_connectors(directory: Path, base_urls: dict[str, str] | None = None) -> list[Connector]:
    """Load every *.yaml in a folder. `base_urls` maps connector id -> URL, so the runner can
    point the same manifests at a server it started on a free port."""
    base_urls = base_urls or {}
    connectors = []
    for path in sorted(Path(directory).glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        try:
            connectors.append(parse_connector(data, base_urls.get(data.get("id") if isinstance(data, dict) else None)))
        except ConnectorError as e:
            raise ConnectorError(f"{path.name}: {e}")
    ids = [c.id for c in connectors]
    names = [a.name for c in connectors for a in c.actions]
    for label, values in (("connector id", ids), ("action name", names)):
        dupes = sorted({v for v in values if values.count(v) > 1})
        if dupes:
            raise ConnectorError(f"duplicate {label}(s): {dupes}")
    unknown = set(base_urls) - set(ids)
    if unknown:
        raise ConnectorError(f"base_urls given for unknown connector(s): {sorted(unknown)}")
    return connectors
