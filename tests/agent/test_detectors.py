"""Detectors: each class fires on the right facts and stays quiet on normal results."""
import pytest

from agent.detectors import Detectors, unexpected_reason
from agent.gate import Decision
from agent.snapshot import PageInfo
from agent.toolbox import ToolResult


def page(url="http://h/erp/bills", title="Bills - Nimbus", head="Bills", password=False, status=200):
    return PageInfo(url, title, head, password, status)


def ok(page_changed=None, p=None, **kw):
    return ToolResult("ok", "ok", page_changed=page_changed, page=p, **kw)


def classes(found):
    return [d.klass for d in found]


def test_a_normal_step_triggers_nothing():
    d = Detectors()
    assert d.inspect("browser_click", {"ref": 1}, ok(True, page())) == []
    assert d.inspect("http_request", {"method": "GET", "url": "/x"}, ok()) == []


def test_tool_error_and_timeout_are_different_classes():
    d = Detectors()
    assert classes(d.inspect("browser_click", {"ref": 1}, ToolResult("x", error=True))) == ["tool_error"]
    assert classes(d.inspect("browser_goto", {"url": "/"}, ToolResult("x", error=True, timeout=True))) == ["timeout"]


def test_noop_fires_for_a_click_that_changed_nothing():
    found = Detectors().inspect("browser_click", {"ref": 7}, ok(False, page()))
    assert classes(found) == ["noop"] and found[0].message.startswith("[DETECTOR] noop:")


def test_noop_points_to_the_api_as_the_way_around_a_dead_control():
    """Seen in a real run: the agent clicked a dead button three times and never tried the API the rules allow."""
    message = Detectors().inspect("browser_click", {"ref": 7}, ok(False, page()))[0].message
    assert "Do not keep clicking it" in message and "API" in message


def test_noop_does_not_fire_for_other_tools_blocked_clicks_or_changed_pages():
    d = Detectors()
    assert d.inspect("browser_type", {"ref": 1, "text": "x"}, ok(False, page())) == []
    assert d.inspect("browser_snapshot", {}, ok(None, page())) == []
    assert d.inspect("browser_click", {"ref": 1}, ok(True, page())) == []
    blocked = ok(False, page(), blocked=[Decision("require_approval", "r", "why")])
    assert d.inspect("browser_click", {"ref": 2}, blocked) == []  # the gate stopped it: not a broken control


@pytest.mark.parametrize("p, fragment", [
    (page(status=404), "HTTP 404"), (page(status=500), "HTTP 500"), (page(status=403), "HTTP 403"),
    (page(title="Error - Nimbus"), "title"), (page(title="404 Not Found"), "title"),
    (page(head="Internal Server Error"), "server error"),
    (page(head="Traceback (most recent call last): ..."), "server error"),
    (page(url="http://h/erp/login"), "login"), (page(url="http://h/Sign-In?next=/"), "login"),
    (page(password=True), "password"),
])
def test_unexpected_pages(p, fragment):
    assert fragment in unexpected_reason(p)
    assert classes(Detectors().inspect("browser_goto", {"url": "/"}, ok(True, p))) == ["unexpected_page"]


@pytest.mark.parametrize("p", [
    page(), page(status=200), page(status=422, head="Due date must be DD/MM/YYYY"),  # form feedback is normal
    page(status=400), page(head="Errors are shown in red when something is wrong"),
])
def test_normal_pages_are_not_flagged(p):
    assert unexpected_reason(p) is None


def test_repeat_needs_three_identical_calls_in_a_row():
    d = Detectors()
    call = ("browser_click", {"ref": 3})
    assert d.inspect(*call, ok(True, page())) == []
    assert d.inspect(*call, ok(True, page())) == []
    assert classes(d.inspect(*call, ok(True, page()))) == ["repeat"]


def test_repeat_is_reset_by_a_different_call_or_different_arguments():
    d = Detectors()
    for args in ({"ref": 3}, {"ref": 3}, {"ref": 4}, {"ref": 3}, {"ref": 3}):
        assert d.inspect("browser_click", args, ok(True, page())) == []
    d2 = Detectors()
    for tool in ("browser_snapshot", "browser_snapshot", "browser_goto", "browser_snapshot"):
        assert d2.inspect(tool, {}, ok()) == []


def test_argument_order_does_not_hide_a_repeat():
    d = Detectors()
    for args in ({"a": 1, "b": 2}, {"b": 2, "a": 1}, {"a": 1, "b": 2}):
        found = d.inspect("http_request", args, ok())
    assert classes(found) == ["repeat"]


def test_retry_budget_is_two_and_the_third_hit_is_exhausted():
    d = Detectors()
    messages = []
    for i in range(3):
        found = d.inspect("browser_click", {"ref": i}, ToolResult("x", error=True))
        messages.append(found[0])
    assert [m.exhausted for m in messages] == [False, False, True]
    assert "retry 1 of 2" in messages[0].message and "retry 2 of 2" in messages[1].message
    assert "ask_human or finish with status blocked" in messages[2].message
    assert d.exhausted() == ["tool_error"]


def test_budgets_are_per_class():
    d = Detectors()
    for i in range(2):
        d.inspect("browser_click", {"ref": i}, ToolResult("x", error=True))
    found = d.inspect("browser_goto", {"url": "/"}, ToolResult("x", error=True, timeout=True))
    assert classes(found) == ["timeout"] and not found[0].exhausted
    assert d.counts == {"tool_error": 2, "timeout": 1, "noop": 0, "unexpected_page": 0, "repeat": 0}
    assert d.exhausted() == []


def test_scattered_tool_errors_do_not_add_up_but_consecutive_ones_do():
    """Seen in a real run: three unrelated slips in 10 steps used up the budget and forced a hand-over."""
    d = Detectors()
    for i in range(6):  # error, success, error, success, ...: never two in a row
        assert d.inspect("read_document", {"source": str(i)}, ToolResult("x", error=True))[0].exhausted is False
        assert d.inspect("browser_goto", {"url": f"/{i}"}, ok(True, page())) == []
    assert d.exhausted() == [] and d.counts["tool_error"] == 0
    found = [d.inspect("note", {"key": str(i)}, ToolResult("x", error=True))[0] for i in range(3)]
    assert [f.exhausted for f in found] == [False, False, True] and d.exhausted() == ["tool_error"]


def test_an_exhausted_budget_stays_exhausted_after_a_later_success():
    d = Detectors()
    for i in range(3):
        d.inspect("browser_click", {"ref": i}, ToolResult("x", error=True))
    d.inspect("ask_human", {"kind": "clarification"}, ok())
    assert d.exhausted() == ["tool_error"]


def test_noop_stays_cumulative_so_a_broken_button_retried_between_other_steps_is_still_caught():
    d = Detectors()
    for i in range(3):
        found = d.inspect("browser_click", {"ref": 9}, ok(False, page()))
        d.inspect("browser_snapshot", {}, ok(None, page()))  # a different, successful step in between
    assert classes(found) == ["noop"] and found[0].exhausted and d.exhausted() == ["noop"]


def test_sending_exactly_the_same_write_again_is_a_repeat_but_a_different_body_is_not():
    """Seen in real runs: after a write stored a different value, the agent re-sent the identical correction four times."""
    d = Detectors()
    edit = ("POST", "/erp/bills/3/edit", "a1b2c3")
    assert d.inspect_writes([edit]) == []
    assert d.inspect_writes([("POST", "/erp/bills/3/edit", "ffffff")]) == []        # a different body: a real change of approach
    again = d.inspect_writes([edit])
    assert classes(again) == ["repeat"] and "retry 1 of 2" in again[0].message and "different notation" in again[0].message
    assert classes(d.inspect_writes([edit])) == ["repeat"]
    third = d.inspect_writes([edit])
    assert third[0].exhausted and d.exhausted() == ["repeat"]


def test_one_result_can_trigger_several_classes():
    d = Detectors()
    found = d.inspect("browser_click", {"ref": 1}, ok(False, page(status=404)))
    assert classes(found) == ["noop", "unexpected_page"]
