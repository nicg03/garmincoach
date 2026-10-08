"""Beta feedback: sending it, the daily ceiling, the admin list and the email."""
from __future__ import annotations

from collections import defaultdict

from fastapi.testclient import TestClient

from server import config, main, mailer, security
from server.main import app
from server.store import open_store

PASSWORD = "password1"


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "site.db")
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({"boss@example.com"}))
    monkeypatch.setattr(config, "ACCOUNT_EMAILS", False)
    monkeypatch.setattr(security, "_attempts", defaultdict(list))
    outbox: list[dict] = []

    def fake_send(to, subject, text):
        outbox.append({"to": to, "subject": subject, "text": text})
        return True
    monkeypatch.setattr(mailer, "send", fake_send)
    return outbox


def _signup(email):
    client = TestClient(app)
    response = client.post("/api/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client, response.json()["user"]


def test_feedback_needs_a_session(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    response = TestClient(app).post("/api/feedback", json={"text": "hello"})
    assert response.status_code == 401


def test_feedback_is_stored_listed_for_admins_and_emailed(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    athlete, _ = _signup("a@example.com")
    boss, _ = _signup("boss@example.com")

    response = athlete.post("/api/feedback", json={
        "text": "  The calendar is confusing  ", "page": "#/training/calendar",
        "user_agent": "TestBrowser/1.0", "lang": "it"})
    assert response.status_code == 200, response.text

    assert athlete.get("/api/admin/feedback").status_code == 404
    items = boss.get("/api/admin/feedback").json()["feedback"]
    assert len(items) == 1
    assert items[0]["email"] == "a@example.com"
    assert items[0]["text"] == "The calendar is confusing"
    assert items[0]["page"] == "#/training/calendar"
    assert items[0]["lang"] == "it"

    assert [m["to"] for m in outbox] == ["boss@example.com"]
    assert "a@example.com" in outbox[0]["subject"]
    assert "The calendar is confusing" in outbox[0]["text"]


def test_feedback_rejects_empty_and_overlong_text(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client, _ = _signup("a@example.com")
    assert client.post("/api/feedback", json={"text": "   "}).status_code == 400
    too_long = "x" * (main.FEEDBACK_MAX_CHARS + 1)
    assert client.post("/api/feedback", json={"text": too_long}).status_code == 400


def test_feedback_has_a_daily_ceiling(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client, _ = _signup("a@example.com")
    for n in range(main.FEEDBACK_DAILY_LIMIT):
        assert client.post("/api/feedback", json={"text": f"note {n}"}).status_code == 200
    assert client.post("/api/feedback", json={"text": "one more"}).status_code == 429


def test_account_delete_removes_feedback(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client, user = _signup("a@example.com")
    client.post("/api/feedback", json={"text": "bye"})
    assert client.post("/api/account/delete",
                       json={"confirm": "a@example.com"}).status_code == 200
    with open_store(config.DB_PATH) as handle:
        left = handle.conn.execute(
            "SELECT COUNT(*) c FROM feedback WHERE user_id = ?", (user["id"],)).fetchone()
    assert left["c"] == 0
