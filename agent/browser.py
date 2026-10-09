"""The browser tool: a real Chromium the agent operates through numbered references.

Purpose: goto / click / type / select / snapshot, each returning an Observation (a fresh
snapshot, notes, anything the gate blocked, and whether the page changed).
Why this design:
  * Every request the page makes goes through `_on_route`, which asks the gate. A request
    the gate refuses never leaves the machine (a refused navigation is answered with an
    empty 204 so the page, and the form the agent filled in, stay as they were; a refused
    sub-request is aborted); the observation tells the agent what was blocked and why.
    Reads (GET) additionally get the redirect guard.
  * Tested finding: Playwright does not route the follow-up requests of a redirect. So the
    `request` event is also watched: a redirect hop the gate would refuse is reported, and
    the page is reset to about:blank so the agent does not act on it. That is detection,
    not prevention (the request already happened), and it is the only protection for
    redirects that follow a write.
  * Downloads are not kept (accept_downloads=False); a click or goto that starts one tells
    the agent to use read_document with that URL. Pop-up pages are closed.
  * After every action the snapshot is compared with the one before it, so a click that
    changes nothing is visible to the `noop` detector.
How it can fail: a stale reference (the page changed since the snapshot) is a clear error;
slow pages give timeouts; the snapshot is only as informative as the page's text and labels.
"""
from dataclasses import dataclass, field

from playwright.sync_api import Browser as PlaywrightBrowser
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from agent.gate import Decision, Gate, GateRequest
from agent.snapshot import EXTRACT_JS, PageInfo, format_snapshot, page_info, snapshot_hash
from agent.web import redirect_chain_problem

__all__ = ["Browser", "Observation", "StaleRefError", "PlaywrightTimeout", "PlaywrightError"]


class StaleRefError(Exception):
    pass


@dataclass
class Observation:
    snapshot: str
    page: PageInfo
    changed: bool
    notes: list[str] = field(default_factory=list)
    blocked: list[Decision] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join([*self.notes, self.snapshot])


class Browser:
    def __init__(self, gate: Gate, chromium: PlaywrightBrowser):
        self.gate = gate
        self._chromium = chromium
        self._playwright = None  # set by launch(), which then also owns Chromium
        self._blocked: list[Decision] = []
        self._violations: list[tuple[Decision, str]] = []
        self._downloads: list[str] = []
        self._doc_status: int | None = None
        self._last_hash: str | None = None

        self._context = chromium.new_context(
            accept_downloads=False, service_workers="block", permissions=[],
            viewport={"width": 1280, "height": 900},
        )
        self.page = self._context.new_page()
        self.page.set_default_timeout(5000)
        self.page.set_default_navigation_timeout(10000)
        self._context.route("**/*", self._on_route)
        self.page.on("request", self._on_request)
        self.page.on("response", self._on_response)
        self.page.on("download", lambda d: self._downloads.append(d.url))
        self._context.on("page", lambda p: p.close() if p is not self.page else None)

    @classmethod
    def launch(cls, gate: Gate, *, headless: bool = True, slow_mo_ms: int = 0) -> "Browser":
        playwright = sync_playwright().start()
        try:
            browser = cls(gate, playwright.chromium.launch(headless=headless, slow_mo=slow_mo_ms))
        except Exception:
            playwright.stop()
            raise
        browser._playwright = playwright
        return browser

    def close(self) -> None:
        try:
            self._context.close()
            if self._playwright is not None:
                self._chromium.close()
                self._playwright.stop()
        except PlaywrightError:
            pass

    # ---- the network layer ------------------------------------------------

    @staticmethod
    def _gate_request(request) -> GateRequest:
        return GateRequest(request.method, request.url, request.post_data_buffer,
                           request.headers.get("content-type"))

    def _on_route(self, route) -> None:
        request = route.request
        try:
            decision = self.gate.evaluate(self._gate_request(request))
            if decision.allowed and request.method.upper() == "GET":
                decision = redirect_chain_problem(self.gate, request.url) or decision
        except Exception as e:  # fail closed: if the check itself breaks, the request does not go out
            decision = Decision("deny", "gate_error", f"the policy check failed ({type(e).__name__})")
        try:
            if decision.allowed:
                route.continue_()
            else:
                self._blocked.append(decision)
                if request.is_navigation_request():
                    # A 204 makes the browser stay on the current page. Aborting instead would replace
                    # it with Chromium's error page, losing the filled-in form and every ref.
                    route.fulfill(status=204)
                else:
                    route.abort()
        except PlaywrightError:
            pass  # the page went away while we were deciding

    def _on_request(self, request) -> None:
        """Playwright does not route redirect hops, but it does report them."""
        if request.redirected_from is None:
            return
        decision = self.gate.evaluate(self._gate_request(request), note="redirect hop the browser followed")
        if not decision.allowed:
            self._violations.append((decision, request.url))

    def _on_response(self, response) -> None:
        try:
            if response.request.is_navigation_request() and response.frame == self.page.main_frame:
                self._doc_status = response.status
        except PlaywrightError:
            pass

    # ---- snapshots ---------------------------------------------------------

    def _snapshot(self) -> tuple[str, PageInfo]:
        for attempt in (1, 2):
            try:
                data = self.page.evaluate(EXTRACT_JS)
                break
            except PlaywrightError:
                if attempt == 2:
                    raise
                self.page.wait_for_timeout(300)  # the page was mid-navigation
        text = format_snapshot(data)
        self._last_hash = snapshot_hash(text)
        return text, page_info(data, self._doc_status)

    def snapshot(self) -> Observation:
        self._blocked.clear()
        text, info = self._snapshot()
        return Observation(text, info, changed=False)

    # ---- actions ----------------------------------------------------------------

    def _locator(self, ref: int):
        locator = self.page.locator(f'[data-vo-ref="{int(ref)}"]')
        if locator.count() == 0:
            raise StaleRefError(f"ref {ref} is not on the current page; the page has changed since the last "
                                f"snapshot, take a new snapshot and use its numbers")
        return locator.first

    def _act(self, action) -> Observation:
        before = self._last_hash
        self._blocked.clear()
        self._violations.clear()
        self._downloads.clear()
        self._doc_status = None
        action()
        try:
            self.page.wait_for_load_state("load", timeout=5000)
        except PlaywrightTimeout:
            pass
        self.page.wait_for_timeout(150)  # late navigations and download events

        blocked, notes = list(self._blocked), []
        if self._violations:
            for decision, url in self._violations:
                blocked.append(decision)
                notes.append(f"BLOCKED after the fact: the browser followed a redirect to {url} "
                             f"({decision.message}). The page was reset to a blank page.")
            self.page.goto("about:blank")
        text, info = self._snapshot()
        notes = [d.message for d in self._blocked] + notes
        for url in dict.fromkeys(self._downloads):
            notes.append(f"That started a file download from {url}. The browser does not keep downloads; "
                         f"use read_document with this URL to read the file.")
        changed = before is None or self._last_hash != before or bool(self._downloads)
        return Observation(text, info, changed=changed, notes=notes, blocked=blocked)

    def goto(self, url: str) -> Observation:
        def go():
            try:
                self.page.goto(url, wait_until="load")
            except PlaywrightError as e:
                if "Download is starting" in str(e):
                    self._downloads.append(url)
                elif not self._blocked:  # a block is reported in the observation, not as an error
                    raise
        return self._act(go)

    def click(self, ref: int) -> Observation:
        return self._act(lambda: self._locator(ref).click())

    def type(self, ref: int, text: str) -> Observation:
        return self._act(lambda: self._locator(ref).fill(text))

    def select(self, ref: int, value: str) -> Observation:
        return self._act(lambda: self._locator(ref).select_option(value))
