from __future__ import annotations

import pytest

from sharebase import create_app
from sharebase.data import store
from sharebase.faults import board
from helpers import OPERATOR, PASSWORD


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Every test starts from a freshly seeded store with no faults armed."""
    monkeypatch.setenv("SHAREBASE_USER", OPERATOR)
    monkeypatch.setenv("SHAREBASE_PASS", PASSWORD)
    monkeypatch.setenv("SHAREBASE_SESSION_TTL", "900")
    monkeypatch.setenv("SHAREBASE_BUSINESS_DATE", "2026-09-29")
    store.reset()
    board.reset()
    yield
    store.reset()
    board.reset()


@pytest.fixture
def app():
    app = create_app()
    app.config.update(TESTING=True)
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def signed_on(client):
    """A client that has completed the sign-on form."""
    response = client.post(
        "/login",
        data={"txtOperator": OPERATOR, "txtPassword": PASSWORD},
        follow_redirects=False,
    )
    assert response.status_code == 302, "sign-on should redirect to the console"
    assert response.headers["Location"].endswith("/console")
    return client
