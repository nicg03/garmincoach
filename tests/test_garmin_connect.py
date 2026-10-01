"""Direct Garmin connection: login with MFA, encrypted tokens, server sync."""
from __future__ import annotations

import json
from datetime import date, timedelta

from fastapi.testclient import TestClient

from garmin_sync import endpoints as ep
from server import config, garmin_connect
from server.main import app
from server.store import open_store

PROFILE = {"displayName": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d"}
TODAY = date.today().isoformat()


class FakeError(Exception):
    pass


class FakeClient:
    def __init__(self):
        self.di_token = None
        self.calls: list[str] = []
        self.sent: list[tuple] = []
        self.expired = False
        self.block_activities = False
        self.fail_activities_after: int | None = None
        self.activity_calls = 0

    def dumps(self):
        return json.dumps({"di_token": self.di_token})

    def loads(self, tokens):
        self.di_token = json.loads(tokens)["di_token"]

    def connectapi(self, path, **kwargs):
        from urllib.parse import urlencode
        params = kwargs.get("params") or {}
        query = path if not params else f"{path}?{urlencode(params)}"
        self.calls.append(query)
        if self.expired:
            raise FakeError("API Error 401 - Unauthorized")
        if path == ep.PROFILE:
            return PROFILE
        if path.startswith("/activitylist-service"):
            self.activity_calls += 1
            if self.block_activities or (
                    self.fail_activities_after is not None
                    and self.activity_calls > self.fail_activities_after):
                raise FakeError("API Error 403 - Forbidden")
            if "start=0" not in query:
                return []
            return [{
                "activityId": 42, "activityName": "Morning Run",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": f"{TODAY} 07:00:00",
                "duration": 3600.0, "distance": 12000.0,
            }]
        if path.startswith("/usersummary-service"):
            return {"totalSteps": 9000, "restingHeartRate": 48}
        if path.startswith("/hrv-service"):
            raise FakeError("API Error 404 - Not Found")
        return {}

    def request(self, method, _domain, path, **kwargs):
        self.sent.append((method, path, kwargs.get("json")))
        body = {"workoutId": 555} if path.endswith("/workout") else {"workoutScheduleId": 777}

        class Response:
            def json(self):
                return body
        return Response()


class FakeGarmin:
    shared = FakeClient()

    def __init__(self, email=None, password=None, return_on_mfa=False):
        self.email = email
        self.password = password
        self.client = FakeGarmin.shared

    def login(self):
        if self.password == "wrong":
            raise type("GarminConnectAuthenticationError", (Exception,), {})("bad")
        if self.email.startswith("mfa"):
            return "needs_mfa", None
        self.client.di_token = "token-1"
        return None, None

    def resume_login(self, _state, code):
        if code != "123456":
            raise FakeError("bad code")
        self.client.di_token = "token-mfa"
        return None, None


def _client(tmp_path, monkeypatch, sync_inline=False):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "site.db")
    monkeypatch.setattr(garmin_connect, "REQUEST_PAUSE_SECONDS", 0)
    monkeypatch.setattr(garmin_connect, "ACTIVITY_LIST_RETRIES", 0)
    monkeypatch.setattr(garmin_connect, "_garmin_class", lambda: FakeGarmin)
    FakeGarmin.shared = FakeClient()
    kicked: list[int] = []

    def fake_kick(user_id, history_rounds=garmin_connect.HISTORY_ROUNDS_TICK):
        kicked.append(user_id)
        if sync_inline:
            garmin_connect.sync(user_id, history_rounds=history_rounds)
        return True
    monkeypatch.setattr(garmin_connect, "kick", fake_kick)
    client = TestClient(app)
    response = client.post("/api/signup", json={
        "email": "a@example.com", "password": "password1"})
    assert response.status_code == 200, response.text
    return client, response.json()["user"], kicked


def test_connect_without_mfa_stores_encrypted_tokens(tmp_path, monkeypatch):
    client, user, kicked = _client(tmp_path, monkeypatch)
    response = client.post("/api/garmin/connect",
                           json={"email": "me@garmin.test", "password": "pw"})
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "connected"
    assert response.json()["connected"] is True
    assert kicked == [user["id"]]
    with open_store(config.DB_PATH) as handle:
        row = handle.garmin_account(user["id"])
    assert "token-1" not in row["tokens"]
    assert "pw" not in json.dumps(row)
    assert json.loads(garmin_connect.decrypt(row["tokens"]))["di_token"] == "token-1"


def test_connect_with_mfa_two_steps(tmp_path, monkeypatch):
    client, _user, _ = _client(tmp_path, monkeypatch)
    first = client.post("/api/garmin/connect",
                        json={"email": "mfa@garmin.test", "password": "pw"}).json()
    assert first["status"] == "mfa"
    bad = client.post("/api/garmin/mfa",
                      json={"challenge_id": first["challenge_id"], "code": "000000"})
    assert bad.status_code == 400
    again = client.post("/api/garmin/connect",
                        json={"email": "mfa@garmin.test", "password": "pw"}).json()
    ok = client.post("/api/garmin/mfa",
                     json={"challenge_id": again["challenge_id"], "code": "123 456"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["connected"] is True


def test_wrong_password_is_reported(tmp_path, monkeypatch):
    client, _user, _ = _client(tmp_path, monkeypatch)
    response = client.post("/api/garmin/connect",
                           json={"email": "me@garmin.test", "password": "wrong"})
    assert response.status_code == 400
    assert "email and password" in response.json()["error"]
    assert client.get("/api/garmin").json()["connected"] is False


def test_sync_fetches_stores_and_walks_history(tmp_path, monkeypatch):
    client, user, _ = _client(tmp_path, monkeypatch, sync_inline=True)
    client.post("/api/garmin/connect", json={"email": "me@garmin.test", "password": "pw"})
    status = client.get("/api/status").json()
    assert status["activities"] == 1
    assert status["days"] >= 1
    assert status["garmin"]["last_error"] is None
    assert status["garmin"]["last_result"]["failures"] > 0  # hrv 404s are tolerated
    assert status["garmin"]["history_done"] is True


def test_expired_session_asks_for_login(tmp_path, monkeypatch):
    client, user, _ = _client(tmp_path, monkeypatch)
    client.post("/api/garmin/connect", json={"email": "me@garmin.test", "password": "pw"})
    FakeGarmin.shared.expired = True
    try:
        garmin_connect.sync(user["id"])
    except garmin_connect.SessionExpired:
        pass
    info = client.get("/api/garmin").json()
    assert info["needs_login"] is True
    assert "sign in again" in info["last_error"]
    assert garmin_connect.due_users() == []


def test_unreadable_tokens_ask_for_login(tmp_path, monkeypatch):
    client, user, _ = _client(tmp_path, monkeypatch)
    client.post("/api/garmin/connect", json={"email": "me@garmin.test", "password": "pw"})
    with open_store(config.DB_PATH) as handle:
        handle.update_garmin_account(user["id"], tokens="not-a-fernet-token")
    try:
        garmin_connect.sync(user["id"])
    except garmin_connect.SessionExpired:
        pass
    info = client.get("/api/garmin").json()
    assert info["needs_login"] is True
    assert info["running"] is False


def test_outbox_workouts_are_written_to_garmin(tmp_path, monkeypatch):
    client, user, _ = _client(tmp_path, monkeypatch)
    client.post("/api/garmin/connect", json={"email": "me@garmin.test", "password": "pw"})
    day = (date.today() + timedelta(days=2)).isoformat()
    response = client.post("/api/workout/schedule", json={
        "date": day,
        "workout": {"name": "Easy 40", "sport": "running", "kind": "easy",
                    "steps": [{"kind": "step", "intensity": "active",
                               "duration": {"type": "time", "value": 40, "unit": "min"},
                               "target": {"type": "hr_zone", "zone": 2}}]},
    })
    assert response.status_code == 200, response.text
    result = garmin_connect.sync(user["id"])
    assert result["written"] == 2
    methods = [(m, p) for m, p, _ in FakeGarmin.shared.sent]
    assert methods[0][1].endswith("/workout")
    assert "/schedule/555" in methods[1][1]


def test_due_users_follow_the_sync_interval(tmp_path, monkeypatch):
    client, user, _ = _client(tmp_path, monkeypatch)
    client.post("/api/garmin/connect", json={"email": "me@garmin.test", "password": "pw"})
    assert garmin_connect.due_users() == [user["id"]]
    garmin_connect.sync(user["id"])
    assert garmin_connect.due_users() == []
    later = garmin_connect.time.time() + config.GARMIN_SYNC_HOURS * 3600 + 1
    assert garmin_connect.due_users(later) == [user["id"]]


def test_lifespan_starts_and_stops_the_scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "site.db")
    with TestClient(app) as client:
        assert client.get("/api/config").status_code == 200


def test_disconnect_and_account_delete_drop_tokens(tmp_path, monkeypatch):
    client, user, _ = _client(tmp_path, monkeypatch)
    client.post("/api/garmin/connect", json={"email": "me@garmin.test", "password": "pw"})
    assert client.delete("/api/garmin/connect").json()["connected"] is False
    client.post("/api/garmin/connect", json={"email": "me@garmin.test", "password": "pw"})
    client.post("/api/account/delete", json={"confirm": "a@example.com"})
    with open_store(config.DB_PATH) as handle:
        assert handle.garmin_account(user["id"]) is None


def test_activity_list_failure_keeps_days_and_retries(tmp_path, monkeypatch):
    client, user, _ = _client(tmp_path, monkeypatch, sync_inline=True)
    client.post("/api/garmin/connect",
                json={"email": "me@garmin.test", "password": "pw"})
    with open_store(config.DB_PATH) as handle:
        before = handle.garmin_account(user["id"])["last_sync"]
    assert before
    FakeGarmin.shared.block_activities = True
    result = garmin_connect.sync(user["id"])
    assert result["activity_list_failed"] is True
    assert result["days"] >= 1
    info = client.get("/api/garmin").json()
    assert info["last_error"]
    assert "recent activities" in info["last_error"]
    with open_store(config.DB_PATH) as handle:
        after = handle.garmin_account(user["id"])
    assert after["last_sync"] == before
    assert garmin_connect.due_users() == [user["id"]]


def test_history_does_not_mark_activities_done_on_list_failure(tmp_path, monkeypatch):
    from server import garmin_fetch
    client, user, _ = _client(tmp_path, monkeypatch)
    client.post("/api/garmin/connect",
                json={"email": "me@garmin.test", "password": "pw"})
    FakeGarmin.shared.fail_activities_after = garmin_fetch.INCREMENTAL_ACTIVITY_PAGES
    garmin_connect.sync(user["id"], history_rounds=1)
    with open_store(config.DB_PATH) as handle:
        backfill = handle.garmin_account(user["id"])["backfill"] or {}
    assert backfill.get("activities_done") is not True
