"""Detectors: plain code that notices when a step went wrong. No LLM.

Purpose: after each tool call, `Detectors.inspect` returns zero or more Detections.
  tool_error       the tool failed (bad arguments, stale reference, server error, ...)
  timeout          the tool timed out
  noop             a click changed nothing on the page (and was not blocked by the gate)
  unexpected_page  the page is a login page or an error page
  repeat           the same tool with the same arguments three times in a row
Each detection becomes a "[DETECTOR] ..." line in the next observation and uses one of that
class's retries. The third detection of one class exhausts its budget (2): the loop must then
force the agent to ask a human or finish as blocked. For tool_error and timeout only
CONSECUTIVE failures count: a call that succeeds resets them (a real run was forced to hand
over after three unrelated slips spread over 10 steps). noop, unexpected_page and repeat stay
cumulative, because a broken control is typically retried with other steps in between.
Why this design: models are good at the next action and bad at noticing they are stuck. The
checks are cheap facts about results (an error flag, an unchanged page hash, a status
code), so they need no judgement and cannot be talked out of firing.
How it can fail: noop also fires for a click that legitimately changes nothing (a link to the
current page); markers like "error" in a page title can be false alarms. Both only cost one
of two retries.
"""
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from agent.snapshot import PageInfo

CLASSES = ("tool_error", "timeout", "noop", "unexpected_page", "repeat")
BUDGET = 2
CONSECUTIVE = ("tool_error", "timeout")  # classes whose count restarts after a call that works
REPEAT_COUNT = 3
TITLE_MARKERS = ("error", "not found", "forbidden", "unauthorized", "access denied", "bad gateway",
                 "unavailable", "traceback")
TEXT_MARKERS = ("internal server error", "traceback (most recent call last)", "bad gateway",
                "service unavailable", "access denied")
LOGIN_PATHS = ("/login", "/signin", "/sign-in", "/auth")
ERROR_STATUSES = (401, 403, 404)  # 4xx like 400/422 are normal form feedback, not "unexpected pages"


@dataclass
class Detection:
    klass: str
    message: str
    exhausted: bool


def unexpected_reason(page: PageInfo) -> str | None:
    if page.status is not None and (page.status in ERROR_STATUSES or page.status >= 500):
        return f"the page answered HTTP {page.status}"
    title, head = page.title.lower(), page.text_head.lower()
    if any(marker in title for marker in TITLE_MARKERS):
        return f"the page title is {page.title!r}"
    if any(marker in head for marker in TEXT_MARKERS):
        return "the page text looks like a server error"
    if any(segment in urlsplit(page.url).path.lower() for segment in LOGIN_PATHS):
        return "this looks like a login page"
    if page.has_password:
        return "the page asks for a password"
    return None


class Detectors:
    def __init__(self, budget: int = BUDGET):
        self.budget = budget
        self.counts = {klass: 0 for klass in CLASSES}
        self._recent: list[str] = []
        self._writes: set[tuple[str, str, str]] = set()

    def exhausted(self) -> list[str]:
        """Classes whose retries are used up."""
        return [k for k, n in self.counts.items() if n > self.budget]

    def _hit(self, klass: str, what: str) -> Detection:
        self.counts[klass] += 1
        n = self.counts[klass]
        if n > self.budget:
            return Detection(klass, f"[DETECTOR] {klass}: {what} The retry budget for this problem "
                                    f"({self.budget}) is used up. You must now ask_human or finish with status blocked.", True)
        return Detection(klass, f"[DETECTOR] {klass}: {what} (retry {n} of {self.budget})", False)

    def inspect_writes(self, writes: list[tuple[str, str, str]]) -> list[Detection]:
        """`writes` are (method, path, body digest) of the writes the gate let through in one step. Sending exactly
        the same write twice is almost always a futile retry (the system stored something else) or an accidental
        duplicate, so it counts as a `repeat`."""
        found = []
        for key in writes:
            if key in self._writes:
                found.append(self._hit("repeat", f"you already sent exactly this {key[0]} {key[1]} earlier. Sending it again "
                                                 "will not give a different result. Change what you send (for example enter "
                                                 "the value in a different notation), use the system's API, or ask the human."))
            self._writes.add(key)
        return found

    def inspect(self, tool: str, arguments: dict, result) -> list[Detection]:
        found = []
        if not result.error and not result.timeout:
            for klass in CONSECUTIVE:
                if self.counts[klass] <= self.budget:  # an exhausted budget stays exhausted
                    self.counts[klass] = 0
        if result.timeout:
            found.append(self._hit("timeout", "the last call timed out. The system may be slow or the page stuck; "
                                              "try something different rather than the same call."))
        elif result.error:
            found.append(self._hit("tool_error", "the last call failed. Do not repeat it unchanged; "
                                                 "check its arguments or use another way."))
        if tool == "browser_click" and result.page_changed is False and not result.blocked and not result.error:
            found.append(self._hit("noop", "the page did not change after your click. The control may be broken or "
                                           "disabled. Do not keep clicking it: if a second look at the page shows nothing "
                                           "wrong with what you entered, treat the web interface as not working and do "
                                           "the same action through the system's API (the company rules allow this)."))
        if result.page is not None:
            reason = unexpected_reason(result.page)
            if reason:
                found.append(self._hit("unexpected_page", f"{reason}. You may not be where you meant to be."))
        self._recent = (self._recent + [json.dumps([tool, arguments], sort_keys=True, default=str)])[-REPEAT_COUNT:]
        if len(self._recent) == REPEAT_COUNT and len(set(self._recent)) == 1:
            found.append(self._hit("repeat", f"you made the same {tool} call {REPEAT_COUNT} times in a row. "
                                             "Do something different or ask the human."))
        return found
