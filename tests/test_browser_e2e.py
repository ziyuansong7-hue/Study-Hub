from __future__ import annotations

import re
from threading import Thread

import pytest
from playwright.sync_api import Page, expect
from werkzeug.serving import make_server

from app import create_app


@pytest.fixture()
def live_server(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("STUDYHUB_LLM_PROVIDER", "fake")
    app = create_app({
        "DATABASE_URL": db_url,
        "TESTING": False,
        "CSRF_ENABLED": True,
    })
    server = make_server("127.0.0.1", 0, app)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


@pytest.mark.e2e
def test_browser_navigation_and_language_switch(page: Page, live_server: str):
    page.goto(live_server)
    expect(page).to_have_title(re.compile("StudyHub"))
    expect(page.locator("html")).to_have_attribute("lang", "en")

    page.get_by_role("link", name="Courses", exact=True).click()
    expect(page).to_have_url(re.compile(r"/courses$"))
    expect(page.locator("h1")).to_be_visible()

    page.get_by_role("button", name="中文").click()
    expect(page.locator("html")).to_have_attribute("lang", "zh-CN")
    expect(page.locator("h1")).to_be_visible()


@pytest.mark.e2e
def test_multi_concept_setup_opens_a_separate_practice_page(page: Page, live_server: str):
    page.goto(f"{live_server}/study")
    concepts = page.locator('input[name="concept_ids"]:visible')
    assert concepts.count() >= 2
    concepts.nth(0).check()
    concepts.nth(1).check()
    expect(concepts.nth(0)).to_be_checked()
    expect(concepts.nth(1)).to_be_checked()
    page.locator('#session-count').fill('2')
    page.get_by_role("button", name="Start session").click()
    expect(page).to_have_url(re.compile(r"/study/session/"))
    expect(page.get_by_role("heading", name="Practice questions")).to_be_visible()
    expect(page.get_by_role("link", name="Back to practice setup")).to_be_visible()
