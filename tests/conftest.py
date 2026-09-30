from __future__ import annotations

import threading

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


# -- live surface fixtures -------------------------------------------------
#
# The surface-layer tests drive a real browser against a real server. Both are
# session-scoped because launching Chromium per test would dominate the run;
# the browser *context* is per-test, so cookies and the application session
# never leak from one test into the next.


@pytest.fixture(scope="session")
def live_server():
    """ShareBase on an ephemeral port, in this process.

    Same process means the autouse reset fixture reaches the very store the
    server is serving from, so a test can seed data and then observe it
    through the browser.
    """
    from werkzeug.serving import make_server

    from sharebase import create_app

    server = make_server("127.0.0.1", 0, create_app(), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def _browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as driver:
        browser = driver.chromium.launch()
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def surface(_browser):
    from surface.browser import BrowserSurface

    context = _browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    try:
        yield BrowserSurface(page)
    finally:
        context.close()


@pytest.fixture
def signed_on_surface(surface, live_server):
    """A surface that has signed on, driven entirely through the surface layer.

    Signing on this way rather than by posting credentials is deliberate: it
    exercises the derived-name path on the login form, which is the same path
    the discovery loop will depend on.
    """
    surface.goto(f"{live_server}/login")
    observation = surface.observe()
    surface.fill(observation.field("Operator ID").ref, OPERATOR)
    surface.fill(observation.field("Password").ref, PASSWORD)
    surface.click(observation.find(role="button", name="Sign On")[0].ref)
    return surface
