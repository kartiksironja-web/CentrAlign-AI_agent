"""The policy gate: decides, in code, whether a request may happen. Never the model.

Purpose: `Gate.evaluate(request)` returns allow, deny(reason) or
require_approval(rule_id, key) for ANY HTTP request the agent could cause, whether it comes
from the browser (route interception) or from an http tool. It never trusts what the agent
says it is doing; it only looks at the request.
Order of checks:
  1. URL must be http(s), absolute, without credentials, and its host:port must belong to a
     connector manifest. Anything else is denied (rule id `host_policy`).
  2. `match` rules from the policy (for example "never DELETE", "never /admin/*") apply to
     EVERY method, GET included. The path is decoded and normalised first, so %61dmin,
     /x/../admin and //admin are all /admin.
  3. A read (GET/HEAD/OPTIONS) that passed 1-2 is allowed.
  4. A write must be mapped by a connector to a named action, or it is denied
     (`unmapped_write`). Its body must be readable (form or JSON), or it is denied
     (`unreadable_request`).
  5. The action's fields are extracted (plus the connector's `lookup`, if a rule needs a
     field the request does not carry). Action rules are evaluated; deny beats
     require_approval beats allow. A field a rule needs that is missing (unless the manifest lists
     it as `optional_fields`) or unreadable counts as the rule applying: a human must approve,
     and the reason is logged.
  6. Look-alikes: if the action has an `entity`, the gate lists the candidate records itself and,
     when another record has a similar name (agent/similar.py), blocks the write with
     require_clarification until a human has answered a question asked AFTER the block.
  7. require_approval: the request is blocked and registered as pending. A human decision is
     stored against an approval key built from the ACTION and its key fields (not the
     channel), so an approval for "this action" covers the browser form and the API alike.
     An approval is single-use: it is consumed when the request is let through.
Fail closed: unreadable bodies, ambiguous duplicate fields, a failed lookup and an unknown record
lead to deny; a missing or non-numeric policy value leads to a human, never to allow.
Every decision is appended to `Gate.log` with its rule id (extracted fields only, never the body).
How it can fail: it can only judge what a connector describes. A write to an endpoint that is
mapped to an action but whose policy fields the manifest forgot to extract is not limited by
those rules; the policy tests assert that every rule fires in at least one scenario. The
clarification check proves a question was asked and answered, not that the agent then followed
the answer; and similar names are judged by shared words only, not by spelling.
"""
import hashlib
import json
import posixpath
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, unquote, urlsplit

import httpx

from agent.connectors import Action, Connector, MatchSpec, origin_of, parse_expression
from agent.policy import Policy, Rule, Unreadable, evaluate_condition, match_rule_applies, to_number
from agent.similar import similar

READ_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class GateRequest:
    method: str
    url: str
    body: str | bytes | None = None
    content_type: str | None = None


@dataclass
class Decision:
    effect: str  # "allow" | "deny" | "require_approval" | "require_clarification"
    rule_id: str | None
    reason: str
    action: str | None = None
    fields: dict = field(default_factory=dict)
    key: str | None = None  # approval or clarification key, when a human is required
    why: str = ""  # set when the gate failed closed: what it could not check

    @property
    def allowed(self) -> bool:
        return self.effect == "allow"

    @property
    def message(self) -> str:
        """The text the tool returns to the agent when the request is blocked."""
        if self.effect == "require_approval":
            extra = f" Why: {self.why}." if self.why else ""
            return f"BLOCKED: approval required (rule {self.rule_id}). Use ask_human(kind=approval).{extra}"
        if self.effect == "require_clarification":
            return (f"BLOCKED: clarification required (rule {self.rule_id}). Use ask_human(kind=clarification). "
                    f"{self.reason}")
        if self.effect == "deny":
            return f"BLOCKED: denied by {self.rule_id or 'the gate'}: {self.reason}"
        return "allowed"


# ---- approvals --------------------------------------------------------------

@dataclass
class Pending:
    key: str
    rule_id: str
    description: str  # written by the gate from the real request, not by the model


class ApprovalStore:
    """Human decisions, keyed by approval key. Single-use for approvals, sticky for denials."""

    def __init__(self):
        self._pending: dict[str, Pending] = {}
        self._approved: set[str] = set()
        self._denied: set[str] = set()

    def state(self, key: str) -> str:
        return "approved" if key in self._approved else "denied" if key in self._denied else "unknown"

    def request(self, key: str, rule_id: str, description: str) -> None:
        if self.state(key) == "unknown":
            self._pending.setdefault(key, Pending(key, rule_id, description))

    def pending(self) -> list[Pending]:
        return list(self._pending.values())

    def decide(self, key: str, approved: bool) -> None:
        if key not in self._pending:
            raise KeyError(f"no pending approval for key {key!r}")
        del self._pending[key]
        (self._approved if approved else self._denied).add(key)

    def consume(self, key: str) -> None:
        self._approved.discard(key)


class ClarificationStore:
    """Questions the gate wants a human to answer before a write may go ahead.

    The gate registers one when a write points at a record that has look-alikes. Only a question the
    human actually answered AFTER the block releases it (`answer_pending`), so an earlier or unrelated
    question never counts. The answer is for the whole group of look-alikes and lasts for the run."""

    def __init__(self):
        self._pending: dict[str, Pending] = {}
        self._answered: set[str] = set()

    def answered(self, key: str) -> bool:
        return key in self._answered

    def request(self, key: str, rule_id: str, description: str) -> None:
        if key not in self._answered:
            self._pending.setdefault(key, Pending(key, rule_id, description))

    def pending(self) -> list[Pending]:
        return list(self._pending.values())

    def answer_pending(self) -> None:
        self._answered.update(self._pending)
        self._pending.clear()


# ---- reading a request ------------------------------------------------------

class _Rejected(Exception):
    def __init__(self, rule_id: str, reason: str):
        super().__init__(reason)
        self.rule_id, self.reason = rule_id, reason


@dataclass
class _Data:
    """Everything an extraction expression can read."""
    form: dict[str, list[str]] = field(default_factory=dict)
    json: dict | None = None
    path: dict[str, str] = field(default_factory=dict)
    query: dict[str, list[str]] = field(default_factory=dict)


def _read(data: _Data, source: str, name: str):
    """One value, or None if absent. Several different values for one name is ambiguous
    (the receiving server might pick another one than we do), so it is rejected."""
    if source == "json":
        return None if data.json is None else data.json.get(name)
    if source == "path":
        return data.path.get(name)
    values = (data.form if source == "form" else data.query).get(name)
    if not values:
        return None
    if len(set(values)) > 1:
        raise _Rejected("unreadable_request", f"field '{name}' is given several different values")
    return values[0]


def _extract(expressions: dict[str, str], data: _Data) -> dict:
    found = {}
    for name, expression in expressions.items():
        for source, key in parse_expression(expression):
            value = _read(data, source, key)
            if value is not None:
                found[name] = value
                break
    return found


def _parse_body(body, content_type) -> tuple[dict[str, list[str]], dict | None]:
    if body is None or body == "" or body == b"":
        return {}, None
    try:
        text = body.decode("utf-8") if isinstance(body, (bytes, bytearray)) else body
    except UnicodeDecodeError:
        raise _Rejected("unreadable_request", "request body is not valid UTF-8")
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind == "application/x-www-form-urlencoded":
        return parse_qs(text, keep_blank_values=True), None
    if kind == "application/json":
        try:
            parsed = json.loads(text)
        except ValueError:
            raise _Rejected("unreadable_request", "request body is not valid JSON")
        if not isinstance(parsed, dict):
            raise _Rejected("unreadable_request", "JSON request body must be an object")
        return {}, parsed
    raise _Rejected("unreadable_request", f"cannot read a write with content type {kind or '(none)'!r}")


def _split_url(url: str) -> tuple[tuple[str, str, int], str, dict[str, list[str]]]:
    if any(ord(c) < 32 for c in url):
        raise _Rejected("host_policy", "URL contains control characters")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise _Rejected("host_policy", f"only absolute http(s) URLs are allowed, got {url[:60]!r}")
    if parts.username or parts.password:
        raise _Rejected("host_policy", "credentials inside a URL are not allowed")
    try:
        origin = origin_of(url)
    except ValueError:
        raise _Rejected("host_policy", "URL has an invalid port")
    path = posixpath.normpath(unquote(parts.path or "/").replace("\\", "/"))
    path = "/" + path.lstrip("/")  # normpath keeps a leading '//' as is
    return origin, path, parse_qs(parts.query, keep_blank_values=True)


def _norm(value) -> str:
    """Normalise a value for an approval key, so 80000, 80000.0 and '80000' are one thing."""
    if value is None:
        return ""
    try:
        number = to_number(value)
        return str(int(number)) if number == int(number) else repr(number)
    except Unreadable:
        return str(value).strip()


def _digest(body: str | bytes | None) -> str:
    """A short fingerprint of a request body, so the log can show that two writes were identical without keeping the bodies."""
    if not body:
        return ""
    return hashlib.sha1(body.encode() if isinstance(body, str) else body).hexdigest()[:10]


def default_fetch(url: str) -> tuple[int, str]:
    response = httpx.get(url, timeout=5.0, follow_redirects=False)
    return response.status_code, response.text


# ---- the gate ---------------------------------------------------------------

class Gate:
    def __init__(self, policy: Policy, connectors: list[Connector], fetch=default_fetch,
                 approvals: ApprovalStore | None = None, clarifications: ClarificationStore | None = None):
        self.policy = policy
        self.connectors = list(connectors)
        self.approvals = approvals or ApprovalStore()
        self.clarifications = clarifications or ClarificationStore()
        self.log: list[dict] = []
        self._fetch = fetch
        self._origins = {c.origin for c in self.connectors}

    @property
    def allowed_hosts(self) -> list[str]:
        return sorted(f"{scheme}://{host}:{port}" for scheme, host, port in self._origins)

    def evaluate(self, request: GateRequest, note: str = "") -> Decision:
        """Judge one request. `note` says why this check happened when it is not a plain
        tool call (for example "redirect hop"); it only goes into the log."""
        try:
            decision = self._decide(request)
        except _Rejected as e:
            decision = Decision("deny", e.rule_id, e.reason)
        self.log.append({
            "method": request.method.upper(), "url": request.url, "effect": decision.effect,
            "rule_id": decision.rule_id, "reason": decision.reason, "action": decision.action,
            "fields": decision.fields, "note": note, "body_digest": _digest(request.body),
        })
        return decision

    def _decide(self, request: GateRequest) -> Decision:
        method = request.method.upper()
        origin, path, query = _split_url(request.url)
        if origin not in self._origins:
            raise _Rejected("host_policy", f"{origin[1]}:{origin[2]} is not in any connector manifest; "
                                           f"allowed: {', '.join(self.allowed_hosts)}")
        for rule in self.policy.rules:
            if rule.match is not None and match_rule_applies(rule, method, path):
                return Decision("deny", rule.id, rule.message or f"request matches rule {rule.id}")
        if method in READ_METHODS:
            return Decision("allow", None, "read-only request to an allowed host")

        form, js = _parse_body(request.body, request.content_type)
        matches = self._map_to_actions(origin, method, path, _Data(form=form, json=js, query=query))
        if not matches:
            raise _Rejected("unmapped_write", f"{method} {path} is not a known action of any connected system")

        needed: dict[str, tuple[Rule, Action, dict, str]] = {}  # approval key -> (rule, action, fields, why)
        all_fields: dict = {}
        for connector, action, data in matches:
            rules = [r for r in self.policy.rules if r.action == action.name]
            fields = _extract(action.extract, data)
            if action.lookup and self._rules_need_lookup(rules, action):
                fields.update(self._lookup(connector, action, data))
            all_fields.update(fields)
            for rule in rules:
                why = ""
                try:
                    required = rule.when is not None and rule.when.field not in action.optional_fields
                    holds = rule.when is None or evaluate_condition(rule.when, fields, required=required)
                except Unreadable as e:
                    # Fail closed: a value the rule needs is missing or unreadable, so we cannot show the rule
                    # does not apply. Treat it as applying; the reason goes to the log and to the human.
                    holds, why = True, f"cannot check rule {rule.id}: {e}"
                if not holds:
                    continue
                if rule.effect == "deny":
                    return Decision("deny", rule.id, why or rule.message or f"action {action.name} is not allowed",
                                    action.name, fields, why=why)
                key = "|".join([action.name] + [f"{k}={_norm(fields.get(k))}" for k in action.approval_key])
                needed.setdefault(key, (rule, action, fields, why))

        for connector, action, data in matches:
            if action.entity is not None:  # a human must say which record is meant when there are look-alikes
                blocked = self._check_entity(connector, action, _extract(action.extract, data))
                if blocked:
                    return blocked

        action_names = ", ".join(a.name for _, a, _ in matches)
        if not needed:
            return Decision("allow", None, f"allowed: {action_names}", action_names, all_fields)
        return self._approvals_step(needed, action_names, all_fields)

    # -- actions, lookups, approvals ----------------------------------------

    def _map_to_actions(self, origin, method, path, data: _Data):
        """Every (connector, action, data) whose match spec fits. A request can be several
        actions at once (an update that also marks paid); all of them are judged."""
        found = []
        for connector in self.connectors:
            if connector.origin != origin:
                continue
            for action in connector.actions:
                for spec in action.match:
                    params = spec.path_params(path) if spec.method == method else None
                    if params is None:
                        continue
                    action_data = _Data(form=data.form, json=data.json, path=params, query=data.query)
                    if self._requirements_met(spec, action_data):
                        found.append((connector, action, action_data))
                        break
        return found

    @staticmethod
    def _requirements_met(spec: MatchSpec, data: _Data) -> bool:
        if not spec.requires_any:
            return True
        for expression in spec.requires_any:
            if any(_read(data, source, key) is not None for source, key in parse_expression(expression)):
                return True
        return False

    @staticmethod
    def _rules_need_lookup(rules: list[Rule], action: Action) -> bool:
        wanted = {r.when.field for r in rules if r.when is not None}
        return bool(wanted & set(action.lookup.extract))

    def _lookup(self, connector: Connector, action: Action, data: _Data) -> dict:
        """Ask the system itself for a field the request does not carry (a GET, not the agent's)."""
        def fail(why: str):
            return _Rejected("lookup_failed", f"could not check the request against policy: {why}")

        def substitute(match: re.Match) -> str:
            name = match.group(1)
            if name not in data.path:
                raise fail(f"path parameter '{name}' missing")
            return quote(data.path[name], safe="")

        url = connector.base_url + re.sub(r"\{path\.(\w+)\}", substitute, action.lookup.url)
        try:
            status, text = self._fetch(url)
            payload = json.loads(text)
        except Exception as e:  # network, timeout, bad JSON: all the same to the gate
            raise fail(f"{type(e).__name__}")
        if status != 200 or not isinstance(payload, dict):
            raise fail(f"lookup answered HTTP {status}")
        found = _extract(action.lookup.extract, _Data(json=payload))
        missing = sorted(set(action.lookup.extract) - set(found))
        if missing:
            raise fail(f"lookup response has no {missing}")
        return found

    def _candidates(self, connector: Connector, action: Action) -> dict[str, str]:
        """id -> name of every record the system lists (a GET by the gate, never by the agent)."""
        entity = action.entity
        try:
            status, text = self._fetch(connector.base_url + entity.candidates_url)
            payload = json.loads(text)
            if status != 200 or not isinstance(payload, list):
                raise ValueError(f"HTTP {status}")
            return {str(r[entity.id_field]).strip(): str(r[entity.name_field]) for r in payload}
        except Exception as e:  # network, bad JSON, missing keys: all the same to the gate
            raise _Rejected("lookup_failed", f"could not list the records to check for look-alikes ({type(e).__name__})")

    def _check_entity(self, connector: Connector, action: Action, fields: dict) -> Decision | None:
        """Block the write until a human has answered a question, if the record it points at has look-alikes."""
        entity = action.entity
        names = self._candidates(connector, action)
        chosen = str(fields.get(entity.field, "")).strip()
        if chosen not in names:  # cannot tell which record is meant: fail closed
            raise _Rejected("entity_unknown", f"{entity.field}={chosen or '(missing)'} is not one of the listed records")
        twins = {i: n for i, n in names.items()
                 if i != chosen and similar(names[chosen], n, entity.ignore_words, entity.min_shared)}
        if not twins:
            return None
        key = f"{action.name}|{entity.field} in {{{','.join(sorted({chosen, *twins}))}}}"
        if self.clarifications.answered(key):
            return None
        others = "; ".join(f"'{n}' ({entity.field}={i})" for i, n in twins.items())
        description = f"'{names[chosen]}' ({entity.field}={chosen}) looks like {others}"
        self.clarifications.request(key, "similar_records", f"{action.name}: {description}")
        return Decision("require_clarification", "similar_records",
                        f"{description}. Ask the requester which one is meant before writing.",
                        action.name, fields, key)

    def _approvals_step(self, needed, action_names: str, all_fields: dict) -> Decision:
        for key, (rule, action, fields, why) in needed.items():
            if self.approvals.state(key) == "denied":
                return Decision("deny", rule.id, "a human denied this action; do not retry it",
                                action.name, fields, key)
        undecided = [(key, rule, action, fields, why) for key, (rule, action, fields, why) in needed.items()
                     if self.approvals.state(key) == "unknown"]
        for key, rule, action, fields, why in undecided:
            summary = ", ".join(f"{k}={fields.get(k, '(not in the request)')}" for k in action.approval_key)
            self.approvals.request(key, rule.id, f"{action.name}: {summary}" + (f" [{why}]" if why else ""))
        if undecided:
            key, rule, action, fields, why = undecided[0]
            return Decision("require_approval", rule.id, why or f"rule {rule.id} needs a human decision",
                            action.name, fields, key, why)
        for key in needed:
            self.approvals.consume(key)
        first_rule = next(iter(needed.values()))[0]
        return Decision("allow", first_rule.id, "approved by a human", action_names, all_fields)
