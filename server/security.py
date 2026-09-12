"""
Accounts and access control.

Two doors, two keys:

  - People sign in with an email and a password. Passwords are stored as
    scrypt hashes; a successful sign-in gets an HMAC-signed cookie carrying
    the user id, so there's no session table to keep and no dependency to
    install -- the signature *is* the session.
  - Each account also gets a long random sync token. That's what a Mac pushes
    with, unattended, over `POST /api/ingest`. It identifies the account by
    itself, so one endpoint serves every user without any of them being able
    to write into another's history.

Signup and sign-in are both throttled per address: a public URL gets probed.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from collections import defaultdict

from fastapi import HTTPException, Request

from . import config, db

COOKIE_NAME = "garmin_session"

# scrypt at these parameters costs ~16 MB and a few tens of milliseconds:
# slow enough to make a stolen database unpleasant to crack, fast enough that
# signing in feels instant.
SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1

MAX_ATTEMPTS = 10
LOCKOUT_SECONDS = 900
_attempts: dict[str, list[float]] = defaultdict(list)


# ---- passwords --------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N,
                            r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def password_matches(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def password_problem(password: str) -> str | None:
    """Why this password can't be used, or None if it's fine."""
    if len(password or "") < config.MIN_PASSWORD:
        return f"Use at least {config.MIN_PASSWORD} characters."
    return None


def email_problem(email: str) -> str | None:
    email = (email or "").strip()
    if "@" not in email or "." not in email.split("@")[-1] or len(email) < 6:
        return "That doesn't look like an email address."
    return None


# ---- sessions ---------------------------------------------------------------
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(payload: bytes) -> str:
    key = db.session_secret().encode()
    return _b64(hmac.new(key, payload, hashlib.sha256).digest())


def issue_session(user_id: int) -> str:
    payload = json.dumps({
        "uid": user_id,
        "exp": int(time.time()) + config.SESSION_DAYS * 86400,
    }).encode()
    return f"{_b64(payload)}.{_sign(payload)}"


def session_user_id(token: str | None) -> int | None:
    """The user this cookie belongs to, or None if it's absent, tampered with
    or expired."""
    if not token:
        return None
    try:
        body, signature = token.split(".", 1)
        payload = _unb64(body)
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        claims = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if claims.get("exp", 0) <= time.time():
        return None
    uid = claims.get("uid")
    return uid if isinstance(uid, int) else None


# ---- throttling -------------------------------------------------------------
def _client(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def locked_out(request: Request) -> bool:
    who = _client(request)
    cutoff = time.time() - LOCKOUT_SECONDS
    _attempts[who] = [t for t in _attempts[who] if t > cutoff]
    return len(_attempts[who]) >= MAX_ATTEMPTS


def record_failure(request: Request) -> None:
    _attempts[_client(request)].append(time.time())


def clear_failures(request: Request) -> None:
    _attempts.pop(_client(request), None)


# ---- FastAPI dependencies ---------------------------------------------------
def current_user(request: Request) -> dict:
    """The signed-in account, or a 401. Every data route depends on this, and
    everything downstream is scoped to the id it returns."""
    user_id = session_user_id(request.cookies.get(COOKIE_NAME))
    if user_id is None:
        raise HTTPException(401, "Not signed in.")
    with db.store() as handle:
        user = handle.user_by_id(user_id)
    if not user:
        # The account was deleted while the cookie was still valid.
        raise HTTPException(401, "Not signed in.")
    return user


def user_from_sync_token(request: Request) -> dict:
    """Identify the account behind a push."""
    header = request.headers.get("authorization", "")
    prefix, _, token = header.partition(" ")
    if prefix.lower() != "bearer" or not token:
        raise HTTPException(401, "Send your sync token as a bearer token.")
    with db.store() as handle:
        user = handle.user_by_token(token.strip())
    if not user:
        raise HTTPException(401, "Unknown sync token.")
    return user


def cookie_kwargs(request: Request) -> dict:
    """Cookie flags, with Secure set only when the connection really is HTTPS
    (Railway terminates TLS and tells us through x-forwarded-proto)."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return {
        "httponly": True,
        "secure": proto == "https",
        "samesite": "lax",
        "max_age": config.SESSION_DAYS * 86400,
        "path": "/",
    }
