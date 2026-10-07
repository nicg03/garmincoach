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
# Past this many tracked addresses, stale entries are swept on the next check.
MAX_TRACKED = 10_000
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


def issue_session(user_id: int, version: int = 0) -> str:
    """`sv` is the account's session version: changing the password bumps it,
    which signs out every cookie issued before."""
    payload = json.dumps({
        "uid": user_id,
        "sv": version,
        "exp": int(time.time()) + config.SESSION_DAYS * 86400,
    }).encode()
    return f"{_b64(payload)}.{_sign(payload)}"


def session_claims(token: str | None) -> dict | None:
    """The claims in this cookie, or None if it's absent, tampered with or
    expired."""
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
    if not isinstance(claims, dict) or claims.get("exp", 0) <= time.time():
        return None
    if not isinstance(claims.get("uid"), int):
        return None
    return claims


# ---- throttling -------------------------------------------------------------
def _client(request: Request) -> str:
    """The caller's address. Each proxy appends to X-Forwarded-For, so only
    the entries our own proxies added can be trusted; anything to their left
    was written by the client and changes freely between attempts."""
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if hops and config.TRUSTED_PROXY_HOPS > 0:
        return hops[-min(config.TRUSTED_PROXY_HOPS, len(hops))]
    return request.client.host if request.client else "unknown"


def _prune(now: float) -> None:
    cutoff = now - LOCKOUT_SECONDS
    for who in [k for k, v in _attempts.items() if not v or v[-1] <= cutoff]:
        _attempts.pop(who, None)


def locked_out(request: Request, bucket: str = "auth") -> bool:
    now = time.time()
    if len(_attempts) > MAX_TRACKED:
        _prune(now)
    who = f"{bucket}:{_client(request)}"
    recent = [t for t in _attempts.get(who, []) if t > now - LOCKOUT_SECONDS]
    if recent:
        _attempts[who] = recent
    else:
        _attempts.pop(who, None)
    return len(recent) >= MAX_ATTEMPTS


def record_failure(request: Request, bucket: str = "auth") -> None:
    _attempts[f"{bucket}:{_client(request)}"].append(time.time())


def clear_failures(request: Request, bucket: str = "auth") -> None:
    _attempts.pop(f"{bucket}:{_client(request)}", None)


# ---- FastAPI dependencies ---------------------------------------------------
def current_user(request: Request) -> dict:
    """The signed-in account, or a 401. Every data route depends on this, and
    everything downstream is scoped to the id it returns."""
    claims = session_claims(request.cookies.get(COOKIE_NAME))
    if claims is None:
        raise HTTPException(401, "Not signed in.")
    with db.store() as handle:
        user = handle.user_by_id(claims["uid"])
    if not user:
        # The account was deleted while the cookie was still valid.
        raise HTTPException(401, "Not signed in.")
    # Cookies from before session versions existed carry no `sv` and count as 0.
    if claims.get("sv", 0) != user.get("session_version", 0):
        raise HTTPException(401, "Signed out: your password or email changed.")
    return user


def is_admin(user: dict) -> bool:
    return (user.get("email") or "").lower() in config.ADMIN_EMAILS


def admin_user(request: Request) -> dict:
    """The signed-in account if it's listed in ADMIN_EMAILS. Anyone else gets
    the same 404 as a route that doesn't exist."""
    user = current_user(request)
    if not is_admin(user):
        raise HTTPException(404, "Not Found")
    return user


def sign_in(request: Request, response, user: dict) -> None:
    response.set_cookie(COOKIE_NAME,
                        issue_session(user["id"], user.get("session_version", 0)),
                        **cookie_kwargs(request))


def clear_session_cookie(request: Request, response) -> None:
    """Drop the session. Flags must match sign-in or the browser keeps the cookie."""
    flags = cookie_kwargs(request)
    response.delete_cookie(COOKIE_NAME,
                           path=flags["path"],
                           secure=flags["secure"],
                           httponly=flags["httponly"],
                           samesite=flags["samesite"])


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
