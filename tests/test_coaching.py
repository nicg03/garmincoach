"""Coach/athlete roles: signup, linking, isolation, assigned workouts."""
from __future__ import annotations

from fastapi.testclient import TestClient

from server import config
from server.main import app
from server.store import open_store


WORKOUT = {
    "name": "Easy 40",
    "sport": "running",
    "kind": "easy",
    "steps": [{
        "kind": "step",
        "intensity": "active",
        "duration": {"type": "time", "value": 40, "unit": "min"},
        "target": {"type": "hr_zone", "zone": 2},
    }],
}


def _clients(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "site.db")
    return TestClient(app), TestClient(app), TestClient(app)


def _signup(client, email, role="athlete"):
    response = client.post("/api/signup", json={
        "email": email, "password": "password1", "role": role,
    })
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_signup_stores_role(tmp_path, monkeypatch):
    athlete_c, coach_c, _ = _clients(tmp_path, monkeypatch)
    athlete = _signup(athlete_c, "a@example.com", "athlete")
    coach = _signup(coach_c, "c@example.com", "coach")
    assert athlete["role"] == "athlete"
    assert coach["role"] == "coach"
    me = coach_c.get("/api/me").json()
    assert me["role"] == "coach"


def test_lookup_only_matches_coach_email(tmp_path, monkeypatch):
    athlete_c, coach_c, other_c = _clients(tmp_path, monkeypatch)
    _signup(athlete_c, "a@example.com")
    coach = _signup(coach_c, "c@example.com", "coach")
    _signup(other_c, "other@example.com")
    miss = athlete_c.get("/api/coaches/lookup", params={"email": "a@example.com"})
    assert miss.status_code == 404
    hit = athlete_c.get("/api/coaches/lookup", params={"email": "c@example.com"})
    assert hit.status_code == 200
    assert hit.json()["id"] == coach["id"]
    assert "sync_token" not in hit.json()


def test_coach_cannot_read_unlinked_athlete(tmp_path, monkeypatch):
    athlete_c, coach_c, _ = _clients(tmp_path, monkeypatch)
    athlete = _signup(athlete_c, "a@example.com")
    _signup(coach_c, "c@example.com", "coach")
    blocked = coach_c.get("/api/metrics", params={"athlete_id": athlete["id"]})
    assert blocked.status_code == 403


def test_link_accept_then_read_and_schedule(tmp_path, monkeypatch):
    athlete_c, coach_c, stranger_c = _clients(tmp_path, monkeypatch)
    athlete = _signup(athlete_c, "a@example.com")
    coach = _signup(coach_c, "c@example.com", "coach")
    _signup(stranger_c, "x@example.com", "coach")

    req = athlete_c.post("/api/coaching/request", json={"coach_id": coach["id"]})
    assert req.status_code == 200
    assert req.json()["status"] == "pending"
    link_id = req.json()["id"]

    inbox = coach_c.get("/api/coaching/inbox").json()
    assert inbox["requests"][0]["athlete_email"] == "a@example.com"

    accepted = coach_c.post(f"/api/coaching/{link_id}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    roster = coach_c.get("/api/coaching/athletes").json()
    assert roster["athletes"][0]["id"] == athlete["id"]

    ok = coach_c.get("/api/metrics", params={"athlete_id": athlete["id"]})
    assert ok.status_code == 200

    stolen = stranger_c.get("/api/metrics", params={"athlete_id": athlete["id"]})
    assert stolen.status_code == 403

    note = coach_c.post(
        f"/api/coaching/athletes/{athlete['id']}/notes",
        json={"kind": "suggestion", "text": "Keep Tuesdays easy."})
    assert note.status_code == 200
    mine = athlete_c.get("/api/coaching/mine").json()
    assert mine["notes"][0]["text"] == "Keep Tuesdays easy."

    scheduled = coach_c.post("/api/workout/schedule", json={
        **WORKOUT, "date": "2026-10-02", "athlete_id": athlete["id"],
    })
    assert scheduled.status_code == 200, scheduled.text
    session = scheduled.json()["session"]
    assert session["assigned_by"] == coach["id"]
    assert session["date"] == "2026-10-02"

    plan = athlete_c.get("/api/plan").json()["plan"]
    assert plan
    found = next(s for s in plan["sessions"] if s["id"] == session["id"])
    assert found["assigned_by"] == coach["id"]


def test_delete_user_clears_links(tmp_path):
    with open_store(tmp_path / "t.db") as handle:
        athlete = handle.create_user("a@b.c", "hash", role="athlete")
        coach = handle.create_user("c@b.c", "hash", role="coach")
        link = handle.upsert_coaching_request(athlete["id"], coach["id"])
        handle.set_coaching_status(link["id"], "accepted")
        handle.add_coach_note(coach["id"], athlete["id"], "comment", "hi")
        handle.delete_user(athlete["id"])
        assert handle.links_for_coach(coach["id"]) == []
        assert handle.notes_for_athlete(athlete["id"]) == []
        assert handle.user_by_id(athlete["id"]) is None
        assert handle.user_by_id(coach["id"]) is not None
