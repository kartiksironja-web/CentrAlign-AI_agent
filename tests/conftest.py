"""Fixtures: every test gets its own freshly seeded data folder and app."""
import pytest
from fastapi.testclient import TestClient

from apps.company.app import create_app
from apps.company.seed import seed_database

ADMIN_TOKEN = "test-admin-token"
ADMIN = {"X-Admin-Token": ADMIN_TOKEN}


@pytest.fixture
def make_client(tmp_path):
    """Factory so a test can choose faults: make_client(faults=["DATE_TRAP"])."""

    def _make(faults=(), admin_token=ADMIN_TOKEN):
        data_dir = tmp_path / "data"
        seed_database(data_dir)
        app = create_app(data_dir, faults=faults, admin_token=admin_token)
        # follow_redirects=False so tests can assert on the redirect itself
        return TestClient(app, follow_redirects=False)

    return _make


@pytest.fixture
def client(make_client):
    return make_client()
