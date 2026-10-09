"""Fixtures for the tool tests: one shared Chromium, a real mock company per test."""
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from agent.browser import Browser
from agent.connectors import load_connectors
from agent.gate import Gate
from agent.human import ScriptedHuman
from agent.policy import load_policy
from agent.toolbox import Toolbox
from apps.company.seed import seed_database
from tests.agent.helpers import PACK
from tests.agent.live import start_company


@pytest.fixture(scope="session")
def chromium():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@dataclass
class Company:
    url: str
    data_dir: Path
    inputs_dir: Path
    gate: Gate

    def query(self, sql, params=()):
        conn = sqlite3.connect(self.data_dir / "company.db")
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql, params)]
        finally:
            conn.close()

    def toolbox(self, human=None, browser=None, **kwargs):
        return Toolbox(self.gate, human or ScriptedHuman({}), default_base_url=self.url, browser=browser,
                       document_roots={"inputs": self.inputs_dir}, **kwargs)


@pytest.fixture
def company(tmp_path):
    """company(faults=[...]) -> Company: the real mock company on a free port, gate pointed at it."""
    stops = []

    def build(faults=()):
        seed_database(tmp_path / "data", inputs_dir=tmp_path / "inputs")
        url, stop = start_company(tmp_path / "data", faults)
        stops.append(stop)
        gate = Gate(load_policy(PACK / "policy.yaml"), load_connectors(PACK / "connectors", {"erp": url, "portal": url}))
        return Company(url, tmp_path / "data", tmp_path / "inputs", gate)

    yield build
    for stop in stops:
        stop()


@pytest.fixture
def browser_for(chromium):
    """browser_for(company) -> Browser (closed after the test)."""
    opened = []

    def build(comp):
        browser = Browser(comp.gate, chromium)
        opened.append(browser)
        return browser

    yield build
    for browser in opened:
        browser.close()
