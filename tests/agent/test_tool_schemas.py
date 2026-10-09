"""Tool schemas must be acceptable to every model provider, including Gemini's strict subset of JSON Schema.

Gemini's function calling accepts a small subset: no free-form objects, no `anyOf`, no `$ref`, no
`default`, no `additionalProperties`. The schemas we publish therefore stay inside that subset for
EVERY provider, and our own validation (pydantic, extra="forbid") still enforces the full rules on arrival.
"""
import json
import urllib.parse

import pytest

from agent.contract import PlanSubmission, SopSelection, TaskContract
from agent.llm import tool_spec
from agent.loop import CONTROL_TOOLS, with_reason
from agent.toolbox import clean_schema
from tests.agent.helpers import BASE, make_gate


def all_published_schemas(company):
    tb = company().toolbox()
    tools = [with_reason(t) for t in tb.openai_tools()]
    tools += [with_reason(tool_spec(name, description, model)) for name, (description, model) in CONTROL_TOOLS.items()]
    tools += [tool_spec("submit_contract", "d", TaskContract), tool_spec("select_rules", "d", SopSelection),
              tool_spec("submit_plan", "d", PlanSubmission)]
    return tools


FORBIDDEN = ("anyOf", "oneOf", "allOf", "$ref", "$defs", "additionalProperties", "default", "title")


def violations(node, path=""):
    found = []
    if isinstance(node, dict):
        if node.get("type") == "object" and not node.get("properties"):
            found.append(f"{path}: an object without properties")
        for key, value in node.items():
            if key in FORBIDDEN:
                found.append(f"{path}.{key}")
            if key == "properties":
                for name, sub in value.items():
                    found += violations(sub, f"{path}.{name}")
            else:
                found += violations(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += violations(value, f"{path}[{i}]")
    return found


def test_every_schema_the_model_sees_stays_inside_the_conservative_subset(company):
    tools = all_published_schemas(company)
    assert len(tools) == 16
    for tool in tools:
        assert violations(tool["function"]["parameters"], tool["function"]["name"]) == [], tool["function"]["name"]


def test_optional_arguments_are_simply_not_required(company):
    by_name = {t["function"]["name"]: t["function"]["parameters"] for t in all_published_schemas(company)}
    assert by_name["ask_human"]["required"] == ["reason", "kind", "question"]  # options is optional
    assert by_name["http_request"]["required"] == ["reason", "method", "url"]  # json and form are optional
    assert by_name["http_request"]["properties"]["json"]["type"] == "string"
    form = by_name["http_request"]["properties"]["form"]
    assert form["type"] == "array" and form["items"]["required"] == ["name", "value"]
    assert by_name["update_plan"]["properties"]["items"]["items"]["properties"]["done"] == {"type": "boolean"}


def test_clean_schema_resolves_references_collapses_optionals_and_keeps_property_names():
    schema = {
        "$defs": {"Item": {"type": "object", "title": "Item", "properties": {"title": {"type": "string"}, "done": {"type": "boolean", "default": False}}}},
        "type": "object", "title": "Args", "additionalProperties": False,
        "properties": {
            "default": {"type": "string", "default": "x"},                                   # a property NAMED default survives
            "maybe": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}], "description": "opt"},
            "item": {"$ref": "#/$defs/Item"},
        },
        "required": ["item"],
    }
    assert clean_schema(schema) == {
        "type": "object",
        "properties": {
            "default": {"type": "string"},
            "maybe": {"type": "array", "items": {"type": "string"}, "description": "opt"},
            "item": {"type": "object", "properties": {"title": {"type": "string"}, "done": {"type": "boolean"}}},
        },
        "required": ["item"],
    }


def test_litellms_real_gemini_conversion_leaves_nothing_gemini_rejects(company):
    """Run LiteLLM's own OpenAI -> Gemini conversion on every tool and look at what would be sent."""
    try:
        from litellm.llms.vertex_ai.gemini.vertex_and_google_ai_studio_gemini import VertexGeminiConfig
        converted = VertexGeminiConfig()._map_function(value=all_published_schemas(company), optional_params={})
    except (ImportError, AttributeError, TypeError) as e:  # a private LiteLLM API: skip loudly if it moves
        pytest.skip(f"LiteLLM's conversion function is not available in this version: {e}")
    declarations = [d for tool in converted for d in tool.get("function_declarations", [])]
    assert len(declarations) == 16
    for declaration in declarations:
        problems = [p for p in violations(declaration["parameters"], declaration["name"]) if not p.endswith(".nullable")]
        assert problems == [], problems


# ---- http_request body forms ------------------------------------------------------------------

def sent(monkeypatch):
    """Capture what http_request would send, without a network."""
    from agent import web
    seen = {}

    def fake_send(method, url, body=None, content_type=None, **kw):
        seen.update(method=method, url=url, body=body, content_type=content_type)
        return web.HttpOutcome(200, "OK", {"content-type": "application/json"}, b"{}", False)

    monkeypatch.setattr(web, "send", fake_send)
    return seen


@pytest.mark.parametrize("arg", ['{"vendor_id": 7, "amount": 5}', {"vendor_id": 7, "amount": 5}])
def test_a_json_body_may_be_json_text_or_an_object(company, monkeypatch, arg):
    seen = sent(monkeypatch)
    result = company().toolbox().call("http_request", {"method": "POST", "url": "/erp/api/bills", "json": arg})
    assert seen["content_type"] == "application/json" and json.loads(seen["body"]) == {"vendor_id": 7, "amount": 5}
    assert not result.error or "HTTP" in result.text


def test_a_form_may_be_a_list_of_pairs_or_a_dict_and_numbers_become_text(company, monkeypatch):
    seen = sent(monkeypatch)
    tb = company().toolbox()
    tb.call("http_request", {"method": "POST", "url": "/erp/bills/new", "form": [
        {"name": "vendor_id", "value": 7}, {"name": "due_date", "value": "03/11/2026"}, {"name": "tag", "value": "a"}, {"name": "tag", "value": "b"}]})
    assert seen["content_type"] == "application/x-www-form-urlencoded"
    assert urllib.parse.parse_qs(seen["body"]) == {"vendor_id": ["7"], "due_date": ["03/11/2026"], "tag": ["a", "b"]}
    tb.call("http_request", {"method": "POST", "url": "/erp/bills/new", "form": {"vendor_id": 7}})
    assert seen["body"] == "vendor_id=7"


def test_empty_optional_values_mean_no_body(company, monkeypatch):
    """Some models send "" or [] for an optional argument they do not use."""
    seen = sent(monkeypatch)
    tb = company().toolbox()
    result = tb.call("http_request", {"method": "GET", "url": "/erp/api/bills", "json": "", "form": []})
    assert not result.error and seen["body"] is None and seen["content_type"] is None


@pytest.mark.parametrize("args, fragment", [
    ({"json": "{not json"}, "json must be valid JSON text"),
    ({"json": "[1, 2]"}, "json must be a JSON object"),
    ({"json": "{}", "form": [{"name": "a", "value": "b"}]}, "json OR form"),
])
def test_bad_bodies_are_clear_tool_errors(company, monkeypatch, args, fragment):
    sent(monkeypatch)
    result = company().toolbox().call("http_request", {"method": "POST", "url": "/erp/api/bills", **args})
    assert result.error and fragment in result.text
