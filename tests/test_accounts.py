"""Account emails: verification, password reset and change, email change,
and signing out other devices."""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict

from fastapi.testclient import TestClient

from server import config, mailer, security
from server.main import app
from server.store import open_store

PASSWORD = "password1"


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "site.db")
    monkeypatch.setattr(config, "PUBLIC_URL", "https://coach.test")
    monkeypatch.setattr(config, "ACCOUNT_EMAILS", True)
    monkeypatch.setattr(security, "_attempts", defaultdict(list))
    outbox: list[dict] = []

    def fake_send(to, subject, text):
        outbox.append({"to": to, "subject": subject, "text": text})
        return True
    monkeypatch.setattr(mailer, "send", fake_send)
    return outbox


def _signup(email="a@example.com"):
    client = TestClient(app)
    response = client.post("/api/signup", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return client, response.json()["user"]


def _login(email="a@example.com", password=PASSWORD):
    client = TestClient(app)
    response = client.post("/api/login", json={"email": email, "password": password})
    return client, response


def _token(message, route):
    match = re.search(rf"https://coach\.test/#/{route}/([\w-]+)", message["text"])
    assert match, message["text"]
    return match.group(1)


def test_account_emails_are_parked_until_a_domain_exists(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ACCOUNT_EMAILS", False)
    _signup()
    assert outbox == []
    assert TestClient(app).get("/api/config").json()["email"] is False


def test_signup_sends_a_verification_link(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    client, user = _signup()
    assert user["email_verified"] is False
    assert [m["to"] for m in outbox] == ["a@example.com"]

    token = _token(outbox[0], "verify")
    response = TestClient(app).post("/api/email/verify", json={"token": token})
    assert response.status_code == 200, response.text
    assert client.get("/api/me").json()["email_verified"] is True


def test_links_are_single_use(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    _signup()
    token = _token(outbox[0], "verify")
    assert TestClient(app).post("/api/email/verify", json={"token": token}).status_code == 200
    assert TestClient(app).post("/api/email/verify", json={"token": token}).status_code == 400


def test_expired_links_are_rejected(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    _signup()
    token = _token(outbox[0], "verify")
    with open_store(config.DB_PATH) as handle:
        handle.conn.execute("UPDATE email_tokens SET expires = ?", (time.time() - 1,))
        handle.commit()
    assert TestClient(app).post("/api/email/verify", json={"token": token}).status_code == 400


def test_resend_is_rate_limited(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    client, _ = _signup()
    assert client.post("/api/email/verify/send").status_code == 429
    with open_store(config.DB_PATH) as handle:
        handle.conn.execute("UPDATE email_tokens SET created = created - 120")
        handle.commit()
    assert client.post("/api/email/verify/send").status_code == 200
    assert len(outbox) == 2
    # Only the newest link works.
    old, new = _token(outbox[0], "verify"), _token(outbox[1], "verify")
    assert TestClient(app).post("/api/email/verify", json={"token": old}).status_code == 400
    assert TestClient(app).post("/api/email/verify", json={"token": new}).status_code == 200


def test_forgot_answers_the_same_for_unknown_emails(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    _signup()
    outbox.clear()
    anon = TestClient(app)
    known = anon.post("/api/password/forgot", json={"email": "a@example.com"})
    unknown = anon.post("/api/password/forgot", json={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    assert [m["to"] for m in outbox] == ["a@example.com"]


def test_reset_signs_out_old_sessions(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    old_device, _ = _signup()
    TestClient(app).post("/api/password/forgot", json={"email": "a@example.com"})
    token = _token(outbox[-1], "reset")

    browser = TestClient(app)
    short = browser.post("/api/password/reset", json={"token": token, "password": "x"})
    assert short.status_code == 400
    # A rejected password doesn't burn the link.
    response = browser.post("/api/password/reset", json={"token": token, "password": "newpassword"})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email_verified"] is True

    assert browser.get("/api/me").status_code == 200
    assert old_device.get("/api/me").status_code == 401
    assert _login(password=PASSWORD)[1].status_code == 401
    assert _login(password="newpassword")[1].status_code == 200
    assert "password was changed" in outbox[-1]["subject"]
    again = browser.post("/api/password/reset", json={"token": token, "password": "another1"})
    assert again.status_code == 400


def test_password_change_keeps_this_device(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    here, _ = _signup()
    elsewhere, _ = _login()

    wrong = here.post("/api/password/change", json={"current": "nope", "password": "newpassword"})
    assert wrong.status_code == 400
    response = here.post("/api/password/change",
                         json={"current": PASSWORD, "password": "newpassword"})
    assert response.status_code == 200, response.text
    assert here.get("/api/me").status_code == 200
    assert elsewhere.get("/api/me").status_code == 401


def test_revoke_signs_out_other_devices(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    here, _ = _signup()
    elsewhere, _ = _login()
    assert here.post("/api/sessions/revoke").status_code == 200
    assert here.get("/api/me").status_code == 200
    assert elsewhere.get("/api/me").status_code == 401


def test_email_change_needs_password_and_a_free_address(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    _signup("taken@example.com")
    client, _ = _signup()
    outbox.clear()

    wrong = client.post("/api/email/change", json={"email": "b@example.com", "password": "nope"})
    assert wrong.status_code == 400
    taken = client.post("/api/email/change",
                        json={"email": "taken@example.com", "password": PASSWORD})
    assert taken.status_code == 409
    assert outbox == []

    response = client.post("/api/email/change",
                           json={"email": "B@example.com", "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert [m["to"] for m in outbox] == ["b@example.com"]
    # Nothing changes until the link is opened.
    assert client.get("/api/me").json()["email"] == "a@example.com"

    token = _token(outbox[0], "email")
    confirmed = client.post("/api/email/change/confirm", json={"token": token})
    assert confirmed.status_code == 200, confirmed.text
    me = client.get("/api/me").json()
    assert me["email"] == "b@example.com" and me["email_verified"] is True
    assert outbox[-1]["to"] == "a@example.com"
    assert _login("b@example.com")[1].status_code == 200


def test_verify_link_for_an_old_address_does_nothing(tmp_path, monkeypatch):
    outbox = _setup(tmp_path, monkeypatch)
    client, _ = _signup()
    verify = _token(outbox[0], "verify")
    client.post("/api/email/change", json={"email": "b@example.com", "password": PASSWORD})
    client.post("/api/email/change/confirm", json={"token": _token(outbox[-1], "email")})
    with open_store(config.DB_PATH) as handle:
        handle.conn.execute("UPDATE users SET email_verified = 0")
        handle.commit()
    assert TestClient(app).post("/api/email/verify", json={"token": verify}).status_code == 400


def test_cookies_without_a_session_version_still_work(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _, user = _signup()
    payload = json.dumps({"uid": user["id"], "exp": int(time.time()) + 3600}).encode()
    legacy = f"{security._b64(payload)}.{security._sign(payload)}"
    client = TestClient(app, cookies={security.COOKIE_NAME: legacy})
    assert client.get("/api/me").status_code == 200


def test_mailer_posts_to_resend(monkeypatch):
    monkeypatch.setattr(config, "ACCOUNT_EMAILS", True)
    monkeypatch.setattr(config, "RESEND_API_KEY", "re_test")
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout, context):
        seen["url"] = request.full_url
        seen["headers"] = dict(request.header_items())
        seen["body"] = json.loads(request.data)
        return Response()
    monkeypatch.setattr(mailer.urllib.request, "urlopen", fake_urlopen)

    assert mailer.reset_password("a@example.com", "https://coach.test/#/reset/abc")
    assert seen["url"] == mailer.RESEND_URL
    assert seen["headers"]["Authorization"] == "Bearer re_test"
    assert seen["headers"]["User-agent"].startswith("gepard.fit")
    assert seen["body"]["to"] == ["a@example.com"]
    assert "https://coach.test/#/reset/abc" in seen["body"]["text"]


def test_pages_move_to_public_url_but_the_api_answers_everywhere(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "CANONICAL_URL", "https://gepard.fit")
    old = TestClient(app, base_url="https://app.up.railway.app")

    page = old.get("/settings?x=1", follow_redirects=False)
    assert page.status_code == 302
    assert page.headers["location"] == "https://gepard.fit/settings?x=1"
    assert old.get("/api/config").status_code == 200
    assert old.post("/api/login", json={"email": "a@example.com",
                                         "password": PASSWORD}).status_code != 302
    assert TestClient(app, base_url="https://gepard.fit").get("/").status_code == 200


def test_no_redirect_without_an_explicit_public_url(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "PUBLIC_URL", "https://app.up.railway.app")
    monkeypatch.setattr(config, "CANONICAL_URL", "")
    custom = TestClient(app, base_url="https://gepard.fit")
    assert custom.get("/", follow_redirects=False).status_code == 200


def test_account_delete_removes_tokens(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client, user = _signup()
    assert client.post("/api/account/delete", json={"confirm": "a@example.com"}).status_code == 200
    with open_store(config.DB_PATH) as handle:
        left = handle.conn.execute(
            "SELECT COUNT(*) c FROM email_tokens WHERE user_id = ?", (user["id"],)).fetchone()
    assert left["c"] == 0


def test_only_admin_emails_see_every_account(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "ADMIN_EMAILS", frozenset({"boss@example.com"}))
    athlete, user = _signup("a@example.com")
    boss, boss_user = _signup("boss@example.com")
    assert user["admin"] is False
    assert boss_user["admin"] is True

    assert athlete.get("/api/admin/users").status_code == 404
    assert TestClient(app).get("/api/admin/users").status_code == 401

    response = boss.get("/api/admin/users")
    assert response.status_code == 200, response.text
    users = response.json()["users"]
    assert [u["email"] for u in users] == ["boss@example.com", "a@example.com"]
    assert set(users[0]) == {"id", "email", "role", "created", "email_verified"}


def test_spoofed_forwarded_for_does_not_reset_the_lockout(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _signup()
    client = TestClient(app)
    for i in range(security.MAX_ATTEMPTS):
        client.post("/api/login", json={"email": "a@example.com", "password": "wrong"},
                    headers={"x-forwarded-for": f"10.0.0.{i}, 203.0.113.7"})
    blocked = client.post("/api/login", json={"email": "a@example.com", "password": PASSWORD},
                          headers={"x-forwarded-for": "10.9.9.9, 203.0.113.7"})
    assert blocked.status_code == 429
    other = client.post("/api/login", json={"email": "a@example.com", "password": PASSWORD},
                        headers={"x-forwarded-for": "198.51.100.1"})
    assert other.status_code == 200


def test_signups_are_throttled_per_address(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    client = TestClient(app)
    codes = [client.post("/api/signup", json={
        "email": f"u{i}@example.com", "password": PASSWORD}).status_code
        for i in range(security.MAX_ATTEMPTS + 1)]
    assert codes[:security.MAX_ATTEMPTS] == [200] * security.MAX_ATTEMPTS
    assert codes[-1] == 429


def test_stale_throttle_entries_are_swept(monkeypatch):
    monkeypatch.setattr(security, "MAX_TRACKED", 2)
    old = time.time() - security.LOCKOUT_SECONDS - 1
    for i in range(5):
        security._attempts[f"auth:1.1.1.{i}"].append(old)

    class Req:
        headers = {}
        client = type("C", (), {"host": "9.9.9.9"})()

    assert security.locked_out(Req()) is False
    assert len(security._attempts) == 0
