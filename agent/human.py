"""The human in the loop: who answers `ask_human`.

Purpose: one small interface (`Human.ask`) with two implementations. ConsoleHuman asks the
person at the keyboard. ScriptedHuman gives pre-written answers for the evaluations and is
always labelled scripted, so a result never passes off scripted help as real human help.
Why this design: the tool code decides WHAT is asked and what the answer means; the human
object only transports the question. For approvals, the question shown to the human
includes a description written by the gate from the real blocked request (see
toolbox.py), not only what the model says it wants.
How it can fail: ScriptedHuman raises NoHumanReply when its script has no answer left for
that kind of question (the tool then tells the agent it cannot continue); ConsoleHuman
blocks waiting for input.
"""
from dataclasses import dataclass, field
from typing import Protocol

from rich.console import Console
from rich.prompt import Prompt

APPROVE_WORDS = {"approve", "approved", "yes", "y", "ok", "go ahead"}
DENY_WORDS = {"deny", "denied", "no", "n", "reject", "rejected"}


class NoHumanReply(Exception):
    pass


@dataclass
class Interaction:
    """One question and answer, for run.json."""
    kind: str
    question: str
    reply: str
    scripted: bool
    options: list[str] | None = None
    context: list[str] = field(default_factory=list)


class Human(Protocol):
    scripted: bool

    def ask(self, kind: str, question: str, options: list[str] | None, context: list[str]) -> str: ...


def is_approval(reply: str) -> bool:
    """Only an explicit yes approves. Anything unclear counts as no."""
    return reply.strip().lower() in APPROVE_WORDS


class ScriptedHuman:
    """Answers in order per kind: ScriptedHuman({"clarification": ["Pune"], "approval": ["deny"]})."""
    scripted = True

    def __init__(self, replies: dict[str, list[str]]):
        self._replies = {kind: list(items) for kind, items in replies.items()}

    def ask(self, kind, question, options, context):
        queue = self._replies.get(kind) or []
        if not queue:
            raise NoHumanReply(f"no scripted reply left for kind={kind!r}")
        return queue.pop(0)


class ConsoleHuman:
    scripted = False

    def __init__(self, console: Console | None = None, stream=None):
        self.console = console or Console()
        self._stream = stream  # for tests; None means the real keyboard

    def ask(self, kind, question, options, context):
        self.console.print(f"\n[bold yellow]The agent needs a human ({kind})[/bold yellow]")
        self.console.print(f"Agent says: {question}", markup=False)
        for line in context:
            self.console.print(line, markup=False)
        if options:
            self.console.print("Options: " + " / ".join(options), markup=False)
        try:
            return Prompt.ask("Your reply", console=self.console, stream=self._stream)
        except EOFError:  # nobody is at the keyboard (input is closed or piped): same as having no human
            raise NoHumanReply("no one can answer: standard input is closed")
