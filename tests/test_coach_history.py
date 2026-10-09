"""AI coach conversations: saved on the server, recalled only when asked,
and an explicit memory the athlete controls."""
from __future__ import annotations

import json
from collections import defaultdict

from fastapi.testclient import TestClient

from server import coach, config, main, security
from server.main import app
from server.store import open_store

PASSWORD = "password1"


class FakeModel:
    """Stands in for OpenAI/Anthropic: records what it was sent."""

    def __init__(self):
        self.replies: list[str] = []
        self.streamed: list[list[dict]] = []
        self.summaries: list[str] = []

    def stream(self, system, messages):
        self.streamed.append(messages)
        reply = self.replies.pop(0) if self.replies else "Easy run today."
        mid = len(reply) // 2
        yield reply[:mid]
        yield reply[mid:]

    def ask(self, system, messages, max_tokens=None):
        self.summaries.append(messages[-1]["content"])
        return "Talked about a sore Achilles; advised easy running."


def _setup(tmp_path, monkeypatch) -> FakeModel:
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "site.db")
    monkeypatch.setattr(config, "ACCOUNT_EMAILS", False)
    monkeypatch.setattr(config, "COACH_DAILY_LIMIT", 0)
    monkeypatch.setattr(security, "_attempts", defaultdict(list))
    monkeypatch.setattr(main, "_check_coach", lambda user: None)
    monkeypatch.setattr(coach, "build_context", lambda user_id: "DATA")
    model = FakeModel()
    monkeypatch.setattr(coach, "_ask_stream", model.stream)
    monkeypatch.setattr(coach, "_ask", model.ask)
    return model


def _signup(email):
    client = TestClient(app)
    response = client.post("/api/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client, response.json()["user"]


def _ask(client, question, **extra) -> list[dict]:
    response = client.post("/api/chat", json={"question": question, "lang": "en", **extra})
    assert response.status_code == 200, response.text
    return [json.loads(line[5:]) for line in response.text.split("\n")
            if line.startswith("data:")]


def _chat_id(events):
    return next(e["chat"]["id"] for e in events if "chat" in e)


def test_first_question_creates_a_chat_and_both_turns_are_saved(tmp_path, monkeypatch):
    model = _setup(tmp_path, monkeypatch)
    model.replies = ["Keep it easy.", "Yes, 40 minutes."]
    client, _ = _signup("a@example.com")

    events = _ask(client, "How should I train this week?")
    chat_id = _chat_id(events)
    assert events[-1] == {"done": True, "text": "Keep it easy."}

    _ask(client, "And tomorrow?", chat_id=chat_id)
    # The follow-up carried the earlier turns, loaded from the database.
    sent = model.streamed[-1]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert "How should I train this week?" in sent[0]["content"]

    body = client.get(f"/api/chats/{chat_id}").json()
    assert body["chat"]["title"] == "How should I train this week?"
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("user", "How should I train this week?"), ("assistant", "Keep it easy."),
        ("user", "And tomorrow?"), ("assistant", "Yes, 40 minutes.")]
    listed = client.get("/api/chats").json()
    assert listed["total"] == 1
    assert listed["chats"][0]["n_messages"] == 4


def test_chats_are_private_to_their_owner(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    alice, _ = _signup("a@example.com")
    bob, _ = _signup("b@example.com")
    chat_id = _chat_id(_ask(alice, "Secret question"))

    assert bob.get(f"/api/chats/{chat_id}").status_code == 404
    assert bob.patch(f"/api/chats/{chat_id}", json={"title": "x"}).status_code == 404
    assert bob.delete(f"/api/chats/{chat_id}").status_code == 404
    assert bob.post("/api/chat", json={"question": "hi", "chat_id": chat_id}).status_code == 404
    assert bob.get("/api/chats").json()["total"] == 0
    assert alice.get(f"/api/chats/{chat_id}").status_code == 200


def test_rename_and_delete(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client, _ = _signup("a@example.com")
    first = _chat_id(_ask(client, "One"))
    _chat_id(_ask(client, "Two"))

    renamed = client.patch(f"/api/chats/{first}", json={"title": "  Achilles  "}).json()
    assert renamed["title"] == "Achilles"
    assert client.delete(f"/api/chats/{first}").status_code == 200
    assert client.get(f"/api/chats/{first}").status_code == 404
    assert client.get("/api/chats").json()["total"] == 1

    assert client.delete("/api/chats").json()["deleted"] == 1
    assert client.get("/api/chats").json()["total"] == 0


def test_long_first_question_gets_a_short_title(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client, _ = _signup("a@example.com")
    chat_id = _chat_id(_ask(client, "word " * 40))
    title = client.get(f"/api/chats/{chat_id}").json()["chat"]["title"]
    assert len(title) <= 60 and title.endswith("…")


def test_remember_line_becomes_a_memory_and_is_stripped(tmp_path, monkeypatch):
    model = _setup(tmp_path, monkeypatch)
    model.replies = ["Noted, I'll keep it in mind.\n\nREMEMBER: Sore left Achilles since October"]
    client, _ = _signup("a@example.com")

    events = _ask(client, "Remember that my left Achilles is sore")
    saved = [e["memory"] for e in events if "memory" in e]
    assert [m["text"] for m in saved] == ["Sore left Achilles since October"]
    assert events[-1]["text"] == "Noted, I'll keep it in mind."

    chat_id = _chat_id(events)
    stored = client.get(f"/api/chats/{chat_id}").json()["messages"][-1]["content"]
    assert "REMEMBER" not in stored

    # The fact travels with the next question, in any chat.
    _ask(client, "What now?")
    assert "Sore left Achilles since October" in model.streamed[-1][0]["content"]


def test_memory_crud_and_ceiling(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client, _ = _signup("a@example.com")
    assert client.post("/api/coach/memory", json={"text": "  "}).status_code == 400

    fact = client.post("/api/coach/memory", json={"text": "Prefers mornings"}).json()
    edited = client.patch(f"/api/coach/memory/{fact['id']}", json={"text": "Runs at 6am"}).json()
    assert edited["text"] == "Runs at 6am"
    assert client.get("/api/coach/memory").json()["memories"][0]["text"] == "Runs at 6am"
    assert client.delete(f"/api/coach/memory/{fact['id']}").status_code == 200
    assert client.get("/api/coach/memory").json()["memories"] == []

    limit = client.get("/api/coach/memory").json()["limit"]
    for n in range(limit):
        assert client.post("/api/coach/memory", json={"text": f"fact {n}"}).status_code == 200
    assert client.post("/api/coach/memory", json={"text": "one more"}).status_code == 409

    other, _ = _signup("b@example.com")
    assert other.get("/api/coach/memory").json()["memories"] == []
    first = client.get("/api/coach/memory").json()["memories"][0]["id"]
    assert other.delete(f"/api/coach/memory/{first}").status_code == 404


def test_earlier_conversations_only_when_switched_on(tmp_path, monkeypatch):
    model = _setup(tmp_path, monkeypatch)
    client, user = _signup("a@example.com")
    old = _chat_id(_ask(client, "My Achilles hurts"))

    _ask(client, "Fresh topic, no recall")
    assert model.summaries == []
    assert "Earlier conversations" not in model.streamed[-1][0]["content"]

    events = _ask(client, "Like we discussed, how's the plan?", use_memory=True)
    assert len(model.summaries) == 2
    assert "My Achilles hurts" in "".join(model.summaries)
    assert "Earlier conversations" in model.streamed[-1][0]["content"]
    assert "sore Achilles" in model.streamed[-1][0]["content"]
    recalling = _chat_id(events)
    assert client.get(f"/api/chats/{recalling}").json()["chat"]["use_memory"] is True

    # Recaps are kept: asking again doesn't re-summarise unchanged chats.
    _ask(client, "And next week?", chat_id=recalling)
    assert len(model.summaries) == 2

    # Summaries don't touch the daily question counter.
    with open_store(config.DB_PATH) as handle:
        calls = handle.conn.execute(
            "SELECT SUM(calls) c FROM coach_usage WHERE user_id = ?",
            (user["id"],)).fetchone()["c"]
    assert calls == 4

    # Switching it off per chat stops the recall.
    client.patch(f"/api/chats/{recalling}", json={"use_memory": False})
    _ask(client, "Anything else?", chat_id=recalling)
    assert "Earlier conversations" not in model.streamed[-1][0]["content"]
    assert old != recalling


def test_failed_answer_keeps_the_question_only(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    def broken(system, messages):
        raise RuntimeError("provider down")
        yield  # pragma: no cover

    monkeypatch.setattr(coach, "_ask_stream", broken)
    client, _ = _signup("a@example.com")
    events = _ask(client, "Hello?")
    assert "provider down" in events[-1]["error"]
    messages = client.get(f"/api/chats/{_chat_id(events)}").json()["messages"]
    assert [m["role"] for m in messages] == ["user"]


def test_account_delete_removes_chats_and_memories(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client, user = _signup("a@example.com")
    _ask(client, "Hi")
    client.post("/api/coach/memory", json={"text": "Prefers mornings"})
    assert client.post("/api/account/delete",
                       json={"confirm": "a@example.com"}).status_code == 200
    with open_store(config.DB_PATH) as handle:
        for table in ("coach_chats", "coach_messages", "coach_memories"):
            left = handle.conn.execute(
                f"SELECT COUNT(*) c FROM {table} WHERE user_id = ?",
                (user["id"],)).fetchone()["c"]
            assert left == 0, table


def test_split_remember_handles_formatting():
    clean, facts = coach.split_remember("Sure.\n**REMEMBER: likes hills**")
    assert clean == "Sure."
    assert facts == ["likes hills"]
    assert coach.split_remember("No marker here.") == ("No marker here.", [])
