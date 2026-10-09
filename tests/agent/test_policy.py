"""Loading policy.yaml and testing single rules."""
import pytest

from agent.policy import (
    Condition, PolicyError, Unreadable, evaluate_condition, load_policy, match_rule_applies, parse_policy,
)
from tests.agent.helpers import PACK

GOOD_RULE = {"id": "r", "action": "a", "when": {"field": "x", "op": ">", "value": 1}, "effect": "require_approval"}


def test_the_company_policy_loads():
    policy = load_policy(PACK / "policy.yaml")
    assert policy.default_host_policy == "deny"
    assert [r.id for r in policy.rules] == [
        "bill_over_limit", "update_over_limit", "paid_over_limit", "long_terms", "no_deletes", "admin_off_limits",
    ]


@pytest.mark.parametrize("data, fragment", [
    ({"default_host_policy": "allow", "rules": []}, "default_host_policy"),
    ({"rules": []}, "default_host_policy"),
    ({"default_host_policy": "deny", "rules": [{**GOOD_RULE, "effect": "allow"}]}, "effect"),
    ({"default_host_policy": "deny", "rules": [{**GOOD_RULE, "op": "~"}]}, "Extra inputs"),
    ({"default_host_policy": "deny", "rules": [{**GOOD_RULE, "typo": 1}]}, "typo"),
    ({"default_host_policy": "deny", "rules": [{"id": "r", "effect": "deny"}]}, "exactly one of"),
    ({"default_host_policy": "deny", "rules": [{**GOOD_RULE, "match": {"method": "GET"}}]}, "exactly one of"),
    ({"default_host_policy": "deny", "rules": [{"id": "r", "match": {"method": "GET"}, "effect": "require_approval"}]}, "effect: deny"),
    ({"default_host_policy": "deny", "rules": [{"id": "r", "match": {}, "effect": "deny"}]}, "method and/or path_glob"),
    ({"default_host_policy": "deny", "rules": [{"id": "r", "action": "a", "effect": "deny", "when": {"field": "x", "op": "~", "value": 1}}]}, "op"),
    ({"default_host_policy": "deny", "rules": [GOOD_RULE, GOOD_RULE]}, "duplicate rule id"),
])
def test_bad_policies_are_rejected_with_a_reason(data, fragment):
    with pytest.raises(PolicyError, match=fragment):
        parse_policy(data)


def rule(**match):
    policy = parse_policy({"default_host_policy": "deny", "rules": [{"id": "m", "match": match, "effect": "deny"}]})
    return policy.rules[0]


@pytest.mark.parametrize("match, method, path, expected", [
    ({"method": "DELETE"}, "DELETE", "/anything", True),
    ({"method": "DELETE"}, "delete", "/anything", True),
    ({"method": "DELETE"}, "GET", "/anything", False),
    ({"path_glob": "/admin/*"}, "GET", "/admin/faults", True),
    ({"path_glob": "/admin/*"}, "POST", "/admin/a/b/c", True),
    ({"path_glob": "/admin/*"}, "GET", "/admin", True),          # the bare prefix is covered too
    ({"path_glob": "/admin/*"}, "GET", "/ADMIN/Faults", True),   # case cannot dodge a deny rule
    ({"path_glob": "/admin/*"}, "GET", "/administrator", False),
    ({"path_glob": "/admin/*"}, "GET", "/erp/admin/x", False),
    ({"method": "POST", "path_glob": "/x/*"}, "POST", "/x/y", True),
    ({"method": "POST", "path_glob": "/x/*"}, "GET", "/x/y", False),  # both parts must match
])
def test_match_rules(match, method, path, expected):
    assert match_rule_applies(rule(**match), method, path) is expected


def cond(op, value, field="amount"):
    return Condition(field=field, op=op, value=value)


@pytest.mark.parametrize("op, value, actual, expected", [
    (">", 50000, 50001, True), (">", 50000, 50000, False), (">", 50000, "50000.5", True),
    (">", 50000, 49999.99, False), (">=", 50000, 50000, True), ("<", 5, "4", True), ("<=", 5, 5, True),
    ("==", 5, "5.0", True), ("!=", 5, 6, True), ("==", "Paid", " Paid ", True), ("!=", "Paid", "Open", True),
    (">", 50000, "1e5", True),
])
def test_conditions(op, value, actual, expected):
    assert evaluate_condition(cond(op, value), {"amount": actual}) is expected


def test_a_missing_field_means_the_rule_does_not_apply():
    assert evaluate_condition(cond(">", 1), {}) is False
    assert evaluate_condition(cond(">", 1), {"amount": None}) is False
    assert evaluate_condition(cond(">", 1), {"other": 99}) is False


@pytest.mark.parametrize("bad", ["nan", "NaN", "inf", "-inf", "abc", "", "5 5", True, [1], {"a": 1}])
def test_a_present_but_unreadable_number_fails_closed(bad):
    """nan > 50000 is False, so letting it through a 'greater than' rule would be a bypass."""
    with pytest.raises(Unreadable):
        evaluate_condition(cond(">", 50000), {"amount": bad})


def test_ordering_against_text_is_a_policy_error_not_a_pass():
    with pytest.raises(Unreadable):
        evaluate_condition(cond(">", "abc"), {"amount": "5"})
