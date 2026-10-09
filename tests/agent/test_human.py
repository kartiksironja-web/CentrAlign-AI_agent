"""The human interface and the ask_human tool."""
import io

import pytest
from rich.console import Console

from agent.gate import GateRequest
from agent.human import ConsoleHuman, NoHumanReply, ScriptedHuman, is_approval
from tests.agent.helpers import BASE, form, make_gate
from agent.toolbox import Toolbox

BIG = dict(amount="80000", vendor_id="5", invoice_no="INV-3107")


def toolbox(human, gate=None):
    return Toolbox(gate or make_gate(), human, default_base_url=BASE)


def ask(tb, kind, question="please", options=None):
    args = {"kind": kind, "question": question}
    if options:
        args["options"] = options
    return tb.call("ask_human", args)


def test_only_an_explicit_yes_approves():
    assert all(is_approval(w) for w in ("approve", " Approved ", "YES", "y", "ok", "go ahead"))
    assert not any(is_approval(w) for w in ("deny", "no", "", "maybe", "approve not", "sure??", "i guess"))


def test_scripted_human_answers_in_order_and_runs_out_loudly():
    h = ScriptedHuman({"clarification": ["Pune", "second"]})
    assert h.scripted is True
    assert h.ask("clarification", "q", None, []) == "Pune" and h.ask("clarification", "q", None, []) == "second"
    with pytest.raises(NoHumanReply):
        h.ask("clarification", "q", None, [])
    with pytest.raises(NoHumanReply):
        h.ask("approval", "q", None, [])


def test_console_human_shows_the_question_and_the_gates_description_and_reads_the_reply():
    out = io.StringIO()
    human = ConsoleHuman(Console(file=out, force_terminal=False, width=100), stream=io.StringIO("approve\n"))
    reply = human.ask("approval", "may I?", ["approve", "deny"], ["What the system would actually do: X"])
    assert reply == "approve" and human.scripted is False
    shown = out.getvalue()
    assert "Agent says: may I?" in shown and "What the system would actually do: X" in shown and "approve / deny" in shown


def test_a_closed_keyboard_means_no_human_not_a_crash(monkeypatch):
    """Seen in a real run with input closed: EOFError became a tool error and the agent retried it three times."""
    def closed(*args, **kwargs):
        raise EOFError("EOF when reading a line")

    monkeypatch.setattr("builtins.input", closed)  # what the real keyboard path calls when stdin is closed
    human = ConsoleHuman(Console(file=io.StringIO(), force_terminal=False, width=100))
    with pytest.raises(NoHumanReply):
        human.ask("clarification", "which one?", None, [])
    result = ask(toolbox(human), "clarification", "which one?")
    assert result.human_unavailable and "HUMAN UNAVAILABLE" in result.text


def test_clarification_is_recorded_and_labelled_scripted():
    tb = toolbox(ScriptedHuman({"clarification": ["The Pune one"]}))
    result = ask(tb, "clarification", "Which Acme?", ["Pune", "Dubai"])
    assert result.text == "HUMAN REPLY: The Pune one" and not result.error
    [interaction] = tb.interactions
    assert (interaction.kind, interaction.reply, interaction.scripted, interaction.options) == (
        "clarification", "The Pune one", True, ["Pune", "Dubai"])


def test_approval_with_nothing_blocked_is_refused_without_bothering_the_human():
    human = ScriptedHuman({"approval": ["approve"]})
    result = ask(toolbox(human), "approval")
    assert result.error and "NOTHING IS WAITING" in result.text
    assert human.ask("approval", "q", None, []) == "approve"  # the scripted answer was not used up


def test_the_human_sees_what_the_gate_recorded_not_what_the_model_claims():
    seen = []

    class Spy(ScriptedHuman):
        def ask(self, kind, question, options, context):
            seen.append((question, context))
            return super().ask(kind, question, options, context)

    gate = make_gate()
    gate.evaluate(form("/erp/bills/new", **BIG))
    tb = toolbox(Spy({"approval": ["approve"]}), gate)
    ask(tb, "approval", "Please approve a small harmless bill")
    question, context = seen[0]
    assert question == "Please approve a small harmless bill"  # the model's words are shown...
    assert context == ["What the system would actually do: create_bill: vendor_id=5, invoice_no=INV-3107, amount=80000",
                       "Rule that stopped it: bill_over_limit"]  # ...next to what the gate saw


def test_approve_unlocks_exactly_the_blocked_request_and_deny_blocks_it_for_good():
    gate = make_gate()
    key = gate.evaluate(form("/erp/bills/new", **BIG)).key
    approved = ask(toolbox(ScriptedHuman({"approval": ["approve"]}), gate), "approval")
    assert "APPROVED by the human" in approved.text and "repeat the blocked request" in approved.text
    assert gate.evaluate(form("/erp/bills/new", **BIG)).allowed

    gate2 = make_gate()
    gate2.evaluate(form("/erp/bills/new", **BIG))
    denied = ask(toolbox(ScriptedHuman({"approval": ["no thanks"]}), gate2), "approval")
    assert "DENIED by the human" in denied.text and "Do not retry" in denied.text
    assert gate2.evaluate(form("/erp/bills/new", **BIG)).effect == "deny"
    assert key  # (the first gate's key existed)


def test_several_pending_items_are_asked_one_by_one():
    gate = make_gate()
    gate.evaluate(form("/erp/bills/new", **BIG))
    gate.evaluate(form("/erp/vendors/new", name="Globex", payment_terms_days="90"))
    tb = toolbox(ScriptedHuman({"approval": ["approve", "deny"]}), gate)
    result = ask(tb, "approval")
    assert "APPROVED by the human: create_bill" in result.text and "DENIED by the human: create_vendor" in result.text
    assert [i.reply for i in tb.interactions] == ["approve", "deny"] and result.summary == "ask_human: 1 approved, 1 denied"


def test_no_reply_available_means_the_agent_must_hand_over():
    tb = toolbox(ScriptedHuman({}))
    result = ask(tb, "clarification")
    assert result.human_unavailable and "needs_human" in result.text and not tb.interactions
    gate = make_gate()
    gate.evaluate(form("/erp/bills/new", **BIG))
    result = ask(toolbox(ScriptedHuman({}), gate), "approval")
    assert result.human_unavailable and gate.approvals.state(gate.approvals.pending()[0].key) == "unknown"


def test_the_kind_is_validated():
    result = toolbox(ScriptedHuman({})).call("ask_human", {"kind": "chat", "question": "hi"})
    assert result.error and "INVALID ARGUMENTS" in result.text
