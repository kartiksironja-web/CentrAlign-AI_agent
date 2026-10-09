"""Connector manifests: loading, strictness, path templates, what the agent may read."""
import pytest

from agent.connectors import ConnectorError, MatchSpec, load_connectors, origin_of, parse_connector, parse_expression
from tests.agent.helpers import PACK

MINIMAL = {"id": "x", "name": "X", "base_url": "http://h:1", "description": "d"}


def test_the_company_manifests_load():
    connectors = {c.id: c for c in load_connectors(PACK / "connectors")}
    assert set(connectors) == {"erp", "portal"}
    assert connectors["erp"].origin == ("http", "127.0.0.1", 8000)
    assert [a.name for a in connectors["erp"].actions] == ["create_bill", "update_bill", "mark_paid", "create_vendor"]
    assert connectors["portal"].actions == ()  # read-only system: every write to it is denied


def test_base_url_can_be_overridden_per_connector():
    connectors = {c.id: c for c in load_connectors(PACK / "connectors", {"erp": "http://127.0.0.1:5555/"})}
    assert connectors["erp"].base_url == "http://127.0.0.1:5555"  # trailing slash dropped
    assert connectors["portal"].base_url == "http://127.0.0.1:8000"


def test_overriding_an_unknown_connector_is_an_error():
    with pytest.raises(ConnectorError, match="unknown connector"):
        load_connectors(PACK / "connectors", {"nope": "http://x:1"})


def test_the_agent_description_has_records_and_capabilities_but_no_gate_internals():
    text = {c.id: c.describe() for c in load_connectors(PACK / "connectors")}["erp"]
    assert "Internal ERP" in text and "Base URL: http://127.0.0.1:8000" in text
    assert "db 'erp', table 'erp_bills'" in text and "invoice_no:" in text
    assert "POST /erp/api/bills" in text
    for internal in ("approval_key", "lookup", "extract", "requires_any", "/admin"):
        assert internal not in text


def test_path_templates():
    spec = MatchSpec(method="post", path="/things/{id}/edit")
    assert spec.method == "POST"
    assert spec.path_params("/things/7/edit") == {"id": "7"}
    assert spec.path_params("/things/7/8/edit") is None  # {id} is one segment
    assert spec.path_params("/things//edit") is None
    assert spec.path_params("/things/7/edit/") is None
    assert MatchSpec(method="GET", path="/a.b").path_params("/aXb") is None  # a dot is a dot


def test_field_expressions():
    assert parse_expression("form.amount|json.amount") == (("form", "amount"), ("json", "amount"))
    assert parse_expression("path.id") == (("path", "id"),)
    for bad in ("amount", "cookie.x", "form.", "form.a b", "form.a|"):
        with pytest.raises(ValueError):
            parse_expression(bad)


def test_origins_use_default_ports_and_ignore_case():
    assert origin_of("http://Example.COM/x") == ("http", "example.com", 80)
    assert origin_of("https://example.com") == ("https", "example.com", 443)
    assert origin_of("http://example.com:81") != origin_of("http://example.com")


@pytest.mark.parametrize("change, fragment", [
    ({"surprise": 1}, "surprise"),
    ({"base_url": "ftp://h"}, "base_url"),
    ({"base_url": "not a url"}, "base_url"),
    ({"actions": [{"name": "a", "match": [{"method": "POST", "path": "no-slash"}]}]}, "start with"),
    ({"actions": [{"name": "a", "match": [{"method": "POST", "path": "/p"}], "extract": {"f": "cookie.x"}}]}, "bad field expression"),
    ({"actions": [{"name": "a", "match": [{"method": "POST", "path": "/p"}],
                   "lookup": {"url": "/q", "extract": {"f": "form.x"}}}]}, "lookup can only read"),
    ({"actions": [{"name": "a", "match": [{"method": "POST", "path": "/p", "requires_any": ["nope"]}]}]}, "bad field expression"),
])
def test_manifests_are_strict(change, fragment):
    with pytest.raises(ConnectorError, match=fragment):
        parse_connector({**MINIMAL, **change})


def test_duplicate_action_names_across_manifests_are_rejected(tmp_path):
    action = "actions: [{name: same, match: [{method: POST, path: /p}]}]"
    (tmp_path / "a.yaml").write_text(f"id: a\nname: A\nbase_url: http://h:1\ndescription: d\n{action}\n")
    (tmp_path / "b.yaml").write_text(f"id: b\nname: B\nbase_url: http://h:1\ndescription: d\n{action}\n")
    with pytest.raises(ConnectorError, match="duplicate action name"):
        load_connectors(tmp_path)


def test_duplicate_connector_ids_are_rejected(tmp_path):
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text("id: same\nname: A\nbase_url: http://h:1\ndescription: d\n")
    with pytest.raises(ConnectorError, match="duplicate connector id"):
        load_connectors(tmp_path)


def test_a_load_error_names_the_file(tmp_path):
    (tmp_path / "broken.yaml").write_text("id: x\n")
    with pytest.raises(ConnectorError, match="broken.yaml"):
        load_connectors(tmp_path)
