"""Plain HTTP for the tools, and the redirect guard for the browser.

Purpose: `send` performs one HTTP request (the tools call the gate first), never following
redirects, never reading more than a size cap. `redirect_chain_problem` checks where a GET
would end up BEFORE the browser makes it.
Why this design: tested against the real browser, Playwright's route handler sees the
request the page makes but NOT the follow-up requests of a redirect, so a redirect from an
allowed host to a forbidden one would slip through. So (1) for every GET the browser is about
to make, the guard follows the redirect chain with its own requests (headers only) and asks
the gate about every hop; (2) the browser also reports hops it did follow (see browser.py).
The tools themselves never follow redirects: the agent sees the 3xx and the next request goes
through the gate like any other.
How it can fail: the guard is a separate request, so a server that answers the guard
differently from the browser could still redirect somewhere else (not the case for a plain
company system). Redirects that follow a WRITE cannot be checked before they happen; they are
only detected afterwards.
"""
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from agent.gate import Decision, Gate, GateRequest

MAX_HOPS = 8


@dataclass
class HttpOutcome:
    status: int
    reason: str
    headers: dict[str, str]
    body: bytes
    truncated: bool


def send(method: str, url: str, body: str | None = None, content_type: str | None = None, *,
         timeout: float = 10.0, max_bytes: int = 1_000_000) -> HttpOutcome:
    headers = {"content-type": content_type} if content_type else {}
    content = body.encode("utf-8") if body is not None else None
    with httpx.stream(method, url, content=content, headers=headers, timeout=timeout,
                      follow_redirects=False) as response:
        chunks, size, truncated = [], 0, False
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            size += len(chunk)
            if size > max_bytes:
                truncated = True
                break
        data = b"".join(chunks)[:max_bytes]
        return HttpOutcome(response.status_code, response.reason_phrase,
                           {k.lower(): v for k, v in response.headers.items()}, data, truncated)


def redirect_chain_problem(gate: Gate, url: str, *, timeout: float = 3.0) -> Decision | None:
    """None if the GET to `url` stays inside what the gate allows; otherwise the denying decision."""
    current = url
    for hop in range(1, MAX_HOPS + 1):
        try:
            with httpx.stream("GET", current, timeout=timeout, follow_redirects=False) as response:
                status, location = response.status_code, response.headers.get("location")
        except httpx.HTTPError as e:
            return Decision("deny", "redirect_guard", f"could not check redirects ({type(e).__name__})")
        if not (300 <= status < 400 and location):
            return None
        current = urljoin(current, location)
        decision = gate.evaluate(GateRequest("GET", current), note=f"redirect hop {hop} of a GET to {url}")
        if not decision.allowed:
            return decision
    return Decision("deny", "redirect_guard", f"more than {MAX_HOPS} redirects")
