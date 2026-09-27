from __future__ import annotations

import re

from app import create_app


def test_browser_posts_require_csrf_token(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_ENABLE_SCHEDULER", "0")
    app = create_app({
        "DATABASE_URL": db_url,
        "TESTING": False,
        "CSRF_ENABLED": True,
    })
    client = app.test_client()

    rejected = client.post("/language/en", data={"next": "/"})
    assert rejected.status_code == 400
    assert b"Invalid or missing CSRF token" in rejected.data

    page = client.get("/")
    match = re.search(rb'name="_csrf_token" value="([^"]+)"', page.data)
    assert match is not None
    accepted = client.post(
        "/language/en",
        data={"next": "/", "_csrf_token": match.group(1).decode()},
    )
    assert accepted.status_code == 302


def test_json_api_is_not_subject_to_browser_form_csrf(db_url, monkeypatch):
    monkeypatch.setenv("STUDYHUB_ENABLE_SCHEDULER", "0")
    app = create_app({
        "DATABASE_URL": db_url,
        "TESTING": False,
        "CSRF_ENABLED": True,
    })
    response = app.test_client().post("/api/attempts", json={})
    assert b"Invalid or missing CSRF token" not in response.data
