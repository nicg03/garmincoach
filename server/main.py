"""
The FastAPI application.

Routes fall into four groups:

  - the page, signup and sign-in
  - read-only data for the dashboard, scoped to the signed-in account
  - the coach, likewise
  - `POST /api/ingest`, where a Mac pushes summaries, identified by the
    account's own sync token rather than a cookie

Everything reads from SQLite and computes on the fly, with no cache to
invalidate. The one piece of background work is the direct Garmin sync
(`garmin_connect.scheduler`), which writes through the same ingest path.
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit
import time

from fastapi import (BackgroundTasks, Body, Depends, FastAPI, HTTPException, Query,
                     Request, Response)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from garmin_sync import metrics

from . import (accounts, backup, coach, coaching, config, db, garmin_connect,
               garmin_export, garmin_fetch, ingest, mailer, security)
from . import store as store_mod
from .store import EmailTaken

STATIC_DIR = config.ROOT / "server" / "static"

# Enough history in front of the visible window for a 42-day CTL to have
# settled before the first plotted point.
WARMUP_DAYS = 60


def startup_notes() -> list[str]:
    """Things worth shouting about in the deploy log rather than discovering
    later through missing data."""
    notes = [f"database: {config.DB_PATH}"]
    if not os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") and os.environ.get("RAILWAY_ENVIRONMENT"):
        notes.append("No volume is attached: this container's disk is wiped on "
                     "every deploy, so attach one and mount it at /data.")
    with db.store() as handle:
        notes.append(f"accounts: {handle.user_count()}")
    notes.append("signup: open" if config.SIGNUP_OPEN else "signup: closed")
    if not config.ACCOUNT_EMAILS:
        notes.append("email: off (set ACCOUNT_EMAILS=1 once a Resend domain is verified)")
    elif config.email_enabled():
        notes.append(f"email: on (Resend, from {config.EMAIL_FROM})")
    else:
        notes.append("email: off, links go to this log (set RESEND_API_KEY)")
    if config.ACCOUNT_EMAILS and not config.PUBLIC_URL:
        notes.append("PUBLIC_URL is not set: email links will use the address "
                     "each request came in on.")
    if config.coach_enabled():
        notes.append(f"coach: on ({config.coach_provider()} / {config.coach_model()})")
    else:
        notes.append("coach: off (set OPENAI_API_KEY or ANTHROPIC_API_KEY)")
    if config.coach_enabled() and config.COACH_DAILY_LIMIT:
        notes.append(f"coach limit: {config.COACH_DAILY_LIMIT} calls per user per day")
    if garmin_connect.available():
        with db.store() as handle:
            connected = len(handle.garmin_connected_users())
        notes.append(f"garmin direct: on, {connected} connected, sync every "
                     f"{config.GARMIN_SYNC_HOURS:g}h"
                     + ("" if config.GARMIN_SCHEDULER else " (scheduler off)"))
        if not config.GARMIN_TOKEN_KEY:
            notes.append("garmin direct: GARMIN_TOKEN_KEY is not set, so the "
                         "token key lives in the database next to the tokens.")
    else:
        notes.append("garmin direct: off (garminconnect not installed)")
    notes.append(f"backups: daily, keeping {config.BACKUP_KEEP} in {backup.backup_dir()}"
                 if config.BACKUP_KEEP > 0 else "backups: off (BACKUP_KEEP=0)")
    if not os.environ.get("TZ"):
        notes.append("TZ is not set: 'today' follows the server clock (UTC on "
                     "Railway), so it turns over hours early or late for users. "
                     "Set TZ, e.g. TZ=Europe/Rome.")
    if not config.SESSION_SECRET:
        notes.append("SESSION_SECRET is not set: the cookie-signing secret lives "
                     "in the database and in every backup of it.")
    return notes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for note in startup_notes():
        print(f"[garmin-sync] {note}", flush=True)
    tasks = []
    if config.GARMIN_SCHEDULER and garmin_connect.available():
        tasks.append(asyncio.create_task(garmin_connect.scheduler()))
    if config.BACKUP_KEEP > 0:
        tasks.append(asyncio.create_task(backup.loop()))
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="gepard.fit", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Gepard-Client"],
)

@app.middleware("http")
async def canonical_host(request: Request, call_next):
    """Send pages opened on any other address (the Railway one, www.) to
    PUBLIC_URL. The API answers everywhere: the extension and the computer
    sync keep posting to whatever address they were linked with."""
    canonical = urlsplit(config.CANONICAL_URL).netloc.lower()
    host = request.headers.get("host", "").lower()
    if (canonical and host and host != canonical
            and request.method in ("GET", "HEAD")
            and not request.url.path.startswith("/api/")):
        target = config.CANONICAL_URL + request.url.path
        if request.url.query:
            target += "?" + request.url.query
        # Not 301: browsers cache those, so a wrong PUBLIC_URL would outlive its fix.
        return RedirectResponse(target, status_code=302)
    return await call_next(request)


class RevalidatedStatic(StaticFiles):
    """JS and CSS must revalidate. Module imports are not covered by the ?v= on main.js."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".html")):
            response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", RevalidatedStatic(directory=STATIC_DIR), name="static")


def _range(days: int | None, frm: str | None, to: str | None) -> tuple[str, str]:
    """Resolve the requested window into a concrete (start, end) pair.

    When `to` is omitted, the end is tomorrow so an activity whose local
    calendar date is ahead of the server's UTC date still appears.
    """
    today = date.today()
    end = to or (today + timedelta(days=1)).isoformat()
    if frm:
        return frm, end
    span = days or config.DEFAULT_WINDOW_DAYS
    origin = today if to is None else date.fromisoformat(end)
    start = (origin - timedelta(days=span - 1)).isoformat()
    return start, end


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "since": user["created"],
        "sync_token": user["sync_token"],
        "role": coaching.role_of(user),
        "email_verified": bool(user.get("email_verified")),
        "admin": security.is_admin(user),
    }


def _auth_body(request: Request, user: dict, **values) -> dict:
    """Account response plus a keychain session for the bundled mobile app."""
    body = {**values, "user": _public_user(user)}
    token = security.mobile_session(request, user)
    if token:
        body["session_token"] = token
    return body


def _subject_id(user: dict, athlete_id: int | None) -> int:
    """Own data, or an accepted athlete's data when the caller is their coach."""
    if athlete_id is None:
        return user["id"]
    coaching.assert_coach_of(user, athlete_id)
    return athlete_id


# ---- page and accounts ------------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/privacy", include_in_schema=False)
def privacy_page():
    return FileResponse(STATIC_DIR / "privacy.html")


@app.get("/support", include_in_schema=False)
def support_page():
    return FileResponse(STATIC_DIR / "support.html")


@app.get("/.well-known/apple-app-site-association", include_in_schema=False)
def apple_app_site_association():
    details = []
    if config.APPLE_TEAM_ID:
        details.append({
            "appID": f"{config.APPLE_TEAM_ID}.{config.MOBILE_APP_ID}",
            # Account emails use the root path plus a hash route. Do not claim
            # /privacy, /support or /pair, which should stay in the browser.
            "components": [{"/": "/", "comment": "gepard.fit account links"}],
        })
    return JSONResponse(
        {"applinks": {"apps": [], "details": details}},
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/.well-known/assetlinks.json", include_in_schema=False)
def android_asset_links():
    associations = []
    if config.ANDROID_APP_LINK_SHA256:
        associations.append({
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": config.MOBILE_APP_ID,
                "sha256_cert_fingerprints": list(config.ANDROID_APP_LINK_SHA256),
            },
        })
    return JSONResponse(
        associations, headers={"Cache-Control": "public, max-age=3600"})


@app.get("/api/config")
def site_config():
    """What the page needs before anyone has signed in."""
    return {
        # The extension popup checks this to recognise the site in the
        # active tab and offer a one-click connect.
        "app": "garmincoach",
        "signup_open": config.SIGNUP_OPEN,
        "coach": config.coach_enabled(),
        "min_password": config.MIN_PASSWORD,
        "repo": config.SYNC_REPO_URL,
        "extension_zip": "/extension.zip",
        "email": config.email_enabled(),
    }


@app.post("/api/signup")
def signup(request: Request, response: Response, background: BackgroundTasks,
           email: str = Body("", embed=True),
           password: str = Body("", embed=True),
           role: str = Body("athlete", embed=True)):
    if not config.SIGNUP_OPEN:
        return JSONResponse({"error": "Signups are closed right now."}, 403)
    if security.locked_out(request) or security.locked_out(request, "signup"):
        return JSONResponse({"error": "Too many attempts. Try again later."}, 429)
    security.record_failure(request, "signup")

    problem = security.email_problem(email) or security.password_problem(password)
    if problem:
        return JSONResponse({"error": problem}, 400)
    chosen = (role or "athlete").strip().lower()
    if chosen not in ("athlete", "coach"):
        return JSONResponse({"error": "Role must be athlete or coach."}, 400)

    try:
        with db.store() as handle:
            user = handle.create_user(email, security.hash_password(password),
                                      role=chosen)
    except EmailTaken:
        # Deliberately explicit: this is a personal tool, not a service where
        # hiding which emails exist buys anything.
        return JSONResponse({"error": "That email already has an account."}, 409)

    security.sign_in(request, response, user)
    if config.ACCOUNT_EMAILS:
        background.add_task(accounts.send_verification, accounts.base_url(request),
                            user["id"])
    return _auth_body(request, user, ok=True)


@app.post("/api/login")
def login(request: Request, response: Response,
          email: str = Body("", embed=True),
          password: str = Body("", embed=True)):
    if security.locked_out(request):
        return JSONResponse({"error": "Too many attempts. Try again later."}, 429)

    with db.store() as handle:
        user = handle.user_by_email(email)
    if not user or not security.password_matches(password, user["password"]):
        security.record_failure(request)
        return JSONResponse({"error": "Wrong email or password."}, 401)

    security.clear_failures(request)
    security.sign_in(request, response, user)
    return _auth_body(request, user, ok=True)


@app.post("/api/logout")
def logout(request: Request, response: Response):
    security.clear_session_cookie(request, response)
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(security.current_user)):
    return _public_user(user)


@app.get("/api/admin/users")
def admin_users(_admin: dict = Depends(security.admin_user)):
    with db.store() as handle:
        users = handle.list_users()
    for user in users:
        user["email_verified"] = bool(user["email_verified"])
    return {"users": users}


FEEDBACK_MAX_CHARS = 2000
FEEDBACK_DAILY_LIMIT = 10


def _notify_feedback(sender: str, text: str, page: str) -> None:
    for admin in sorted(config.ADMIN_EMAILS):
        mailer.feedback_received(admin, sender, text, page)


@app.post("/api/feedback")
def send_feedback(background: BackgroundTasks,
                  text: str = Body("", embed=True),
                  page: str = Body("", embed=True),
                  user_agent: str = Body("", embed=True),
                  lang: str = Body("", embed=True),
                  user: dict = Depends(security.current_user)):
    text = (text or "").strip()
    if not text:
        return JSONResponse({"error": "Write something first."}, 400)
    if len(text) > FEEDBACK_MAX_CHARS:
        return JSONResponse(
            {"error": f"Keep it under {FEEDBACK_MAX_CHARS} characters."}, 400)
    with db.store() as handle:
        if handle.feedback_count_today(user["id"]) >= FEEDBACK_DAILY_LIMIT:
            return JSONResponse(
                {"error": "That's plenty for today, thank you. Try again tomorrow."}, 429)
        handle.add_feedback(user["id"], text, (page or "")[:200],
                            (user_agent or "")[:300], (lang or "")[:10])
    background.add_task(_notify_feedback, user["email"], text, (page or "")[:200])
    return {"ok": True}


@app.get("/api/admin/feedback")
def admin_feedback(_admin: dict = Depends(security.admin_user)):
    with db.store() as handle:
        return {"feedback": handle.list_feedback()}


@app.post("/api/token/rotate")
def rotate_token(user: dict = Depends(security.current_user)):
    """Invalidates the old token immediately, so a leaked one stops working."""
    with db.store() as handle:
        token = handle.rotate_token(user["id"])
    return {"sync_token": token}


# ---- email, password, sessions ----------------------------------------------
LINK_INVALID = "This link is invalid, expired or already used."


@app.post("/api/email/verify/send")
def verify_send(request: Request, background: BackgroundTasks,
                user: dict = Depends(security.current_user)):
    if user["email_verified"]:
        return {"ok": True, "verified": True}
    if accounts.recently_sent(user["id"], "verify"):
        return JSONResponse(
            {"error": "An email just went out. Give it a minute."}, 429)
    background.add_task(accounts.send_verification, accounts.base_url(request),
                        user["id"])
    return {"ok": True}


@app.post("/api/email/verify")
def verify_email(token: str = Body("", embed=True)):
    with db.store() as handle:
        row = handle.consume_email_token(token, "verify")
        user = handle.user_by_id(row["user_id"]) if row else None
        if not user or row["new_email"] != user["email"]:
            return JSONResponse({"error": LINK_INVALID}, 400)
        handle.mark_verified(user["id"])
    return {"ok": True, "email": user["email"]}


@app.post("/api/password/forgot")
def password_forgot(request: Request, background: BackgroundTasks,
                    email: str = Body("", embed=True)):
    """The same answer whether or not the address has an account, so this
    can't be used to find out who signed up."""
    if security.locked_out(request, "forgot"):
        return JSONResponse({"error": "Too many attempts. Try again later."}, 429)
    security.record_failure(request, "forgot")
    if not security.email_problem(email):
        background.add_task(accounts.send_reset, accounts.base_url(request), email)
    return {"ok": True}


@app.post("/api/password/reset")
def password_reset(request: Request, response: Response,
                   background: BackgroundTasks,
                   token: str = Body("", embed=True),
                   password: str = Body("", embed=True)):
    problem = security.password_problem(password)
    if problem:
        return JSONResponse({"error": problem}, 400)
    with db.store() as handle:
        row = handle.consume_email_token(token, "reset")
        if not row or not handle.user_by_id(row["user_id"]):
            return JSONResponse({"error": LINK_INVALID}, 400)
        handle.set_password(row["user_id"], security.hash_password(password))
        # Following the link proved the inbox is theirs.
        handle.mark_verified(row["user_id"])
        handle.bump_session_version(row["user_id"])
        user = handle.user_by_id(row["user_id"])
    security.clear_failures(request)
    security.sign_in(request, response, user)
    background.add_task(accounts.notify_change, user["email"], "password")
    return _auth_body(request, user, ok=True)


@app.post("/api/password/change")
def password_change(request: Request, response: Response,
                    background: BackgroundTasks,
                    current: str = Body("", embed=True),
                    password: str = Body("", embed=True),
                    user: dict = Depends(security.current_user)):
    if security.locked_out(request):
        return JSONResponse({"error": "Too many attempts. Try again later."}, 429)
    if not security.password_matches(current, user["password"]):
        security.record_failure(request)
        return JSONResponse({"error": "Your current password is wrong."}, 400)
    problem = security.password_problem(password)
    if problem:
        return JSONResponse({"error": problem}, 400)
    with db.store() as handle:
        handle.set_password(user["id"], security.hash_password(password))
        handle.bump_session_version(user["id"])
        user = handle.user_by_id(user["id"])
    # Every other device is signed out; this one gets a fresh cookie.
    security.sign_in(request, response, user)
    background.add_task(accounts.notify_change, user["email"], "password")
    return _auth_body(request, user, ok=True)


@app.post("/api/email/change")
def email_change(request: Request, background: BackgroundTasks,
                 email: str = Body("", embed=True),
                 password: str = Body("", embed=True),
                 user: dict = Depends(security.current_user)):
    if security.locked_out(request):
        return JSONResponse({"error": "Too many attempts. Try again later."}, 429)
    if not security.password_matches(password, user["password"]):
        security.record_failure(request)
        return JSONResponse({"error": "Your password is wrong."}, 400)
    problem = security.email_problem(email)
    if problem:
        return JSONResponse({"error": problem}, 400)
    new_email = email.strip().lower()
    if new_email == user["email"]:
        return JSONResponse({"error": "That's already your email."}, 400)
    with db.store() as handle:
        taken = handle.user_by_email(new_email) is not None
    if taken:
        return JSONResponse({"error": "That email already has an account."}, 409)
    if accounts.recently_sent(user["id"], "email_change"):
        return JSONResponse(
            {"error": "An email just went out. Give it a minute."}, 429)
    background.add_task(accounts.send_email_change, accounts.base_url(request),
                        user["id"], new_email)
    return {"ok": True, "pending": new_email}


@app.post("/api/email/change/confirm")
def email_change_confirm(request: Request, response: Response,
                         background: BackgroundTasks,
                         token: str = Body("", embed=True)):
    with db.store() as handle:
        row = handle.consume_email_token(token, "email_change")
        old = handle.user_by_id(row["user_id"]) if row else None
        if not old or not row["new_email"]:
            return JSONResponse({"error": LINK_INVALID}, 400)
        try:
            handle.set_email(old["id"], row["new_email"])
        except EmailTaken:
            return JSONResponse(
                {"error": "That email got an account in the meantime."}, 409)
        handle.mark_verified(old["id"])
        handle.bump_session_version(old["id"])
        user = handle.user_by_id(old["id"])
    security.sign_in(request, response, user)
    background.add_task(accounts.notify_change, old["email"], "email address")
    return _auth_body(request, user, ok=True)


@app.post("/api/sessions/revoke")
def sessions_revoke(request: Request, response: Response,
                    user: dict = Depends(security.current_user)):
    """Signs out every other device; this one gets a fresh cookie."""
    with db.store() as handle:
        handle.bump_session_version(user["id"])
        user = handle.user_by_id(user["id"])
    security.sign_in(request, response, user)
    return _auth_body(request, user, ok=True)


# ---- Mac ↔ site linking -----------------------------------------------------
@app.post("/api/pair/begin")
def pair_begin():
    """A Mac starts linking. No auth: the code is only useful once someone
    already signed into the site approves it in their browser."""
    with db.store() as handle:
        return handle.create_pairing()


@app.post("/api/pair/approve")
def pair_approve(user_code: str = Body("", embed=True),
                 user: dict = Depends(security.current_user)):
    with db.store() as handle:
        ok = handle.approve_pairing(user_code, user["id"])
    if not ok:
        return JSONResponse(
            {"error": "That code is unknown, expired, or already used."}, 400)
    return {"ok": True}


@app.post("/api/pair/poll")
def pair_poll(device_code: str = Body("", embed=True)):
    """The Mac asks until the owner has approved. On success the sync token
    is returned once and the pairing is burned."""
    with db.store() as handle:
        result = handle.poll_pairing(device_code)
    if result is None:
        return JSONResponse({"error": "Unknown or expired code."}, 404)
    if result.get("pending"):
        return {"status": "pending"}
    return {
        "status": "ready",
        "sync_token": result["sync_token"],
        "email": result["email"],
    }


@app.get("/api/pair/{user_code}")
def pair_status(user_code: str):
    """What the pair page needs before anyone has clicked Approve.

    Registered after the fixed /api/pair/* routes so a typo like GET
    /api/pair/begin cannot be mistaken for a user code.
    """
    if user_code.lower() in {"begin", "approve", "poll"}:
        return JSONResponse({"error": "Use POST for this endpoint."}, 405)
    with db.store() as handle:
        pairing = handle.pairing_by_user_code(user_code)
    if not pairing:
        return JSONResponse({"error": "Unknown or expired code."}, 404)
    return {
        "user_code": pairing["user_code"],
        "expires_in": max(0, int(pairing["expires"] - time.time())),
        "ready": pairing["user_id"] is None and not pairing["consumed"],
    }


@app.get("/pair/{user_code}", include_in_schema=False)
def pair_page(user_code: str):
    return FileResponse(STATIC_DIR / "pair.html")


@app.post("/api/account/delete")
def delete_account(request: Request, response: Response, confirm: str = Body("", embed=True),
                   user: dict = Depends(security.current_user)):
    if confirm.strip().lower() != user["email"]:
        return JSONResponse(
            {"error": "Type your email address to confirm."}, 400)
    with db.store() as handle:
        handle.delete_user(user["id"])
    security.clear_session_cookie(request, response)
    return {"ok": True}


# ---- data -------------------------------------------------------------------
@app.get("/api/status")
def status(user: dict = Depends(security.current_user)):
    info = db.status(user["id"])
    last = info.get("last")
    today = date.today()
    if last:
        gap = (today - date.fromisoformat(last)).days
        info["ago"] = "today" if gap == 0 else ("yesterday" if gap == 1
                                                else f"{gap} days ago")
        info["stale"] = gap > 2
    else:
        info["ago"] = ""
        info["stale"] = True
    last_activity = info.get("last_activity")
    if last_activity:
        try:
            act_gap = (today - date.fromisoformat(last_activity)).days
        except ValueError:
            act_gap = 0
        if act_gap > 2:
            info["stale"] = True
    info["coach"] = config.coach_enabled()
    info["coach_limit"] = config.COACH_DAILY_LIMIT
    info["coach_used"] = (db.coach_calls_today(user["id"])
                          if config.COACH_DAILY_LIMIT else 0)
    info["user"] = _public_user(user)
    info["repo"] = config.SYNC_REPO_URL
    garmin_connect.refresh_if_stale(user["id"])
    info["garmin"] = garmin_connect.connection(user["id"])
    return info


@app.get("/api/days")
def days(days: int | None = Query(None, ge=1, le=3650),
         frm: str | None = Query(None, alias="from"),
         to: str | None = Query(None),
         user: dict = Depends(security.current_user)):
    start, end = _range(days, frm, to)
    return {"days": db.window(user["id"], start, end)[0]}


@app.get("/api/activities")
def activities(days: int | None = Query(None, ge=1, le=3650),
               frm: str | None = Query(None, alias="from"),
               to: str | None = Query(None),
               user: dict = Depends(security.current_user)):
    start, end = _range(days, frm, to)
    return {"activities": db.window(user["id"], start, end)[1]}


@app.get("/api/metrics")
def dashboard_metrics(days: int | None = Query(None, ge=1, le=3650),
                      frm: str | None = Query(None, alias="from"),
                      to: str | None = Query(None),
                      athlete_id: int | None = Query(None),
                      user: dict = Depends(security.current_user)):
    """Aggregated series for the charts.

    Reads a warmup period before the visible window so the smoothed curves
    start where they belong, then trims it back off.
    """
    uid = _subject_id(user, athlete_id)
    start, end = _range(days, frm, to)
    # Asking for a year when you only have a month of history would otherwise
    # draw eleven empty months, so the window shrinks to the data you have.
    # The warmup is clamped the same way: padding with days that predate the
    # history would seed the smoothed curves with rest days that never
    # happened, and the coach -- which reads from the first stored day -- would
    # then quote different numbers than the charts.
    first = db.status(uid).get("first")
    if first and first > start:
        start = first
    warmup = (date.fromisoformat(start) - timedelta(days=WARMUP_DAYS)).isoformat()
    if first and warmup < first:
        warmup = first

    day_rows, activity_rows = db.window(uid, warmup, end)
    payload = metrics.build(day_rows, activity_rows, start=warmup, end=end,
                            visible_from=start)
    payload["window"] = {"from": start, "to": end}
    payload["recent_activities"] = list(reversed(activity_rows))[:25]
    return payload


# ---- performance, plans, workouts ------------------------------------------
from . import planning


@app.get("/api/performance")
def performance_view(athlete_id: int | None = Query(None),
                     user: dict = Depends(security.current_user)):
    return db.full_performance(_subject_id(user, athlete_id))


@app.get("/api/athlete")
def get_athlete(athlete_id: int | None = Query(None),
                user: dict = Depends(security.current_user)):
    return planning.athlete(_subject_id(user, athlete_id))


@app.put("/api/athlete")
def put_athlete(payload: dict[str, Any] = Body(...),
                user: dict = Depends(security.current_user)):
    return planning.save_athlete(user["id"], payload)


@app.get("/api/races")
def get_races(athlete_id: int | None = Query(None),
              user: dict = Depends(security.current_user)):
    return {"races": planning.list_races(_subject_id(user, athlete_id))}


@app.post("/api/races")
def post_race(payload: dict[str, Any] = Body(...),
              user: dict = Depends(security.current_user)):
    try:
        return planning.save_race(user["id"], payload)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)


@app.delete("/api/races/{race_id}")
def remove_race(race_id: str, user: dict = Depends(security.current_user)):
    planning.delete_race(user["id"], race_id)
    return {"ok": True}


@app.get("/api/plan")
def get_plan(plan_id: str | None = None,
             athlete_id: int | None = Query(None),
             user: dict = Depends(security.current_user)):
    uid = _subject_id(user, athlete_id)
    plan = planning.handle_get_plan(uid, plan_id)
    if not plan:
        return {"plan": None, "adherence": planning.adherence(uid)}
    return {"plan": plan, "adherence": planning.adherence(uid)}


@app.get("/api/insights")
def get_insights(user: dict = Depends(security.current_user)):
    return planning.pace_insights(user["id"])


@app.post("/api/insights")
def post_insights(payload: dict[str, Any] = Body(...),
                  user: dict = Depends(security.current_user)):
    try:
        return planning.apply_pace_insights(user["id"], payload.get("action") or "")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)


@app.post("/api/plan/generate")
def post_plan(payload: dict[str, Any] = Body(...),
              user: dict = Depends(security.current_user)):
    race_id = payload.get("race_id")
    if not race_id:
        return JSONResponse({"error": "Pick a race first."}, 400)
    try:
        plan = planning.generate_plan(
            user["id"], race_id,
            extras=payload.get("extras"),
            notes=payload.get("notes"))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)
    if payload.get("adjust") and payload.get("notes"):
        planning.llm_patches(user["id"], plan["id"], payload["notes"])
        plan = planning.handle_get_plan(user["id"], plan["id"])
    return {"plan": plan}


@app.post("/api/plan/{plan_id}/activate")
def activate(plan_id: str, user: dict = Depends(security.current_user)):
    try:
        return {"plan": planning.activate_plan(user["id"], plan_id)}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)


@app.post("/api/plan/{plan_id}/patch")
def patch(plan_id: str, payload: dict[str, Any] = Body(...),
          user: dict = Depends(security.current_user)):
    try:
        return {"plan": planning.patch_plan(user["id"], plan_id, payload)}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)


@app.patch("/api/plan/session/{session_id}")
def patch_session(session_id: str, payload: dict[str, Any] = Body(...),
                  user: dict = Depends(security.current_user)):
    try:
        return planning.update_session(user["id"], session_id, payload)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)


@app.get("/api/workouts")
def get_workouts(user: dict = Depends(security.current_user)):
    paces = db.full_performance(user["id"], with_meta=False).get("paces") or {}
    return {"workouts": planning.templates(user["id"], paces)}


@app.post("/api/workouts")
def post_workout(payload: dict[str, Any] = Body(...),
                 user: dict = Depends(security.current_user)):
    try:
        return planning.save_template(user["id"], payload)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)


@app.post("/api/workout/preview")
def preview_workout(payload: dict[str, Any] = Body(...),
                    user: dict = Depends(security.current_user)):
    from garmin_sync.garmin_workout import to_garmin
    from garmin_sync.workout_dsl import describe, validate
    try:
        clean = validate(payload.get("workout") or payload)
        return {"workout": clean, "garmin": to_garmin(clean),
                "description": describe(clean)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)


@app.post("/api/workout/schedule")
def schedule_one(payload: dict[str, Any] = Body(...),
                 user: dict = Depends(security.current_user)):
    """Queue a one-off workout on a date, or put it in place of `session_id`."""
    from garmin_sync.workout_dsl import validate
    try:
        workout = validate(payload.get("workout") or payload)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)
    day = (payload.get("date") or date.today().isoformat())[:10]
    athlete_id = payload.get("athlete_id")
    uid = _subject_id(user, int(athlete_id) if athlete_id is not None else None)
    assigned_by = user["id"] if athlete_id is not None else None
    if payload.get("session_id"):
        try:
            session = planning.replace_session(
                uid, str(payload["session_id"]), workout, assigned_by=assigned_by)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        return {"session": session, "replaced": True}
    session = planning.schedule_workout(uid, workout, day, assigned_by=assigned_by)
    return {"session": session}


@app.post("/api/plan/session/{session_id}/restore")
def restore_session(session_id: str, user: dict = Depends(security.current_user)):
    try:
        return {"session": planning.restore_session(user["id"], session_id)}
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)


@app.get("/api/day/decide")
def get_decision(user: dict = Depends(security.current_user)):
    return planning.decide_today(user["id"])


@app.post("/api/day/decide")
def post_decision(payload: dict[str, Any] = Body(None),
                  user: dict = Depends(security.current_user)):
    return planning.apply_decision(user["id"], (payload or {}).get("action"))


@app.get("/api/races/{race_id}/review")
def review_race(race_id: str, user: dict = Depends(security.current_user)):
    try:
        return planning.race_review(user["id"], race_id)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)


# ---- human coach / athlete --------------------------------------------------
@app.get("/api/coaches/lookup")
def coaches_lookup(email: str = Query(""),
                   user: dict = Depends(security.current_user)):
    found = coaching.lookup_coach(email)
    if not found:
        return JSONResponse({"error": "No coach with that email."}, 404)
    return found


@app.post("/api/coaching/request")
def coaching_request(payload: dict[str, Any] = Body(...),
                     user: dict = Depends(security.current_user)):
    coach_id = payload.get("coach_id")
    if coach_id is None:
        return JSONResponse({"error": "Pick a coach first."}, 400)
    return coaching.request_link(user, int(coach_id))


@app.get("/api/coaching/mine")
def coaching_mine(user: dict = Depends(security.current_user)):
    return coaching.athlete_links(user)


@app.get("/api/coaching/inbox")
def coaching_inbox(user: dict = Depends(security.current_user)):
    return coaching.inbox(user)


@app.post("/api/coaching/{link_id}/accept")
def coaching_accept(link_id: int, user: dict = Depends(security.current_user)):
    return coaching.set_link_status(user, link_id, "accepted")


@app.post("/api/coaching/{link_id}/reject")
def coaching_reject(link_id: int, user: dict = Depends(security.current_user)):
    return coaching.set_link_status(user, link_id, "rejected")


@app.get("/api/coaching/athletes")
def coaching_athletes(user: dict = Depends(security.current_user)):
    return coaching.roster(user)


@app.get("/api/coaching/athletes/{athlete_id}")
def coaching_athlete(athlete_id: int, user: dict = Depends(security.current_user)):
    return coaching.athlete_overview(user, athlete_id)


@app.get("/api/coaching/athletes/{athlete_id}/notes")
def coaching_notes(athlete_id: int, user: dict = Depends(security.current_user)):
    return coaching.list_notes(user, athlete_id)


@app.post("/api/coaching/athletes/{athlete_id}/notes")
def coaching_add_note(athlete_id: int, payload: dict[str, Any] = Body(...),
                      user: dict = Depends(security.current_user)):
    return coaching.add_note(
        user, athlete_id,
        payload.get("kind") or "comment",
        payload.get("text") or "",
        payload.get("session_id"))


# ---- coach ------------------------------------------------------------------
def _check_coach(user: dict) -> None:
    if not config.coach_enabled():
        raise HTTPException(
            503, "The coach is off: set OPENAI_API_KEY (or ANTHROPIC_API_KEY) to switch it on.")
    # Asking about an empty history wastes a call to say "there's no history".
    if not db.status(user["id"])["days"]:
        raise HTTPException(
            400, "Publish some data first -- there's nothing to talk about yet.")
    limit = config.COACH_DAILY_LIMIT
    if limit and db.coach_calls_today(user["id"]) >= limit:
        raise HTTPException(
            429, f"You've used today's {limit} coach questions. Back tomorrow.")


@app.get("/api/brief")
def brief(refresh: int = 0, lang: str = "en",
          user: dict = Depends(security.current_user)):
    """Today's briefing, generated once and cached for the rest of the day."""
    lang = coach.normalize_lang(lang)
    cached = db.get_briefing(user["id"], date.today().isoformat(), lang)
    if cached and not refresh:
        return {"date": date.today().isoformat(), "text": cached,
                "cached": True, "lang": lang}

    _check_coach(user)
    try:
        result = coach.cached_briefing(user["id"], refresh=bool(refresh), lang=lang)
    except Exception as e:  # noqa: BLE001 - surface the provider's complaint
        raise HTTPException(502, f"The coach couldn't answer: {e}") from None
    db.record_coach_call(user["id"])
    return result


@app.post("/api/chat")
def chat(payload: dict[str, Any] = Body(...),
         user: dict = Depends(security.current_user)):
    _check_coach(user)
    question = (payload.get("question") or "").strip()[:2000]
    if not question:
        raise HTTPException(400, "Ask something.")
    lang = coach.normalize_lang(payload.get("lang"))
    chat_id = payload.get("chat_id")
    if chat_id:
        current = db.get_chat(user["id"], _int_id(chat_id))
        if not current:
            raise HTTPException(404, "No such conversation.")
        history = db.chat_messages(user["id"], current["id"],
                                   last=coach.MAX_HISTORY_TURNS)
    else:
        current = db.create_chat(user["id"], question, lang,
                                 bool(payload.get("use_memory")))
        history = []
    db.add_chat_message(user["id"], current["id"], "user", question)
    db.record_coach_call(user["id"])
    return StreamingResponse(
        coach.stream(user["id"], current, question, history, lang=lang),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _int_id(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise HTTPException(404, "No such conversation.") from None


@app.get("/api/chats")
def chats_list(offset: int = 0, limit: int = 20,
               user: dict = Depends(security.current_user)):
    with db.store() as handle:
        return handle.list_chats(user["id"], max(1, min(limit, 50)), max(0, offset))


@app.delete("/api/chats")
def chats_delete_all(user: dict = Depends(security.current_user)):
    with db.store() as handle:
        return {"deleted": handle.delete_all_chats(user["id"])}


@app.get("/api/chats/{chat_id}")
def chats_get(chat_id: int, user: dict = Depends(security.current_user)):
    found = db.get_chat(user["id"], chat_id)
    if not found:
        raise HTTPException(404, "No such conversation.")
    found.pop("summary", None)
    found.pop("summary_upto", None)
    return {"chat": found, "messages": db.chat_messages(user["id"], chat_id)}


@app.patch("/api/chats/{chat_id}")
def chats_update(chat_id: int, payload: dict[str, Any] = Body(...),
                 user: dict = Depends(security.current_user)):
    title = payload.get("title")
    use_memory = payload.get("use_memory")
    with db.store() as handle:
        found = handle.update_chat(
            user["id"], chat_id,
            title=title if isinstance(title, str) else None,
            use_memory=use_memory if isinstance(use_memory, bool) else None)
    if not found:
        raise HTTPException(404, "No such conversation.")
    found.pop("summary", None)
    found.pop("summary_upto", None)
    return found


@app.delete("/api/chats/{chat_id}")
def chats_delete(chat_id: int, user: dict = Depends(security.current_user)):
    with db.store() as handle:
        if not handle.delete_chat(user["id"], chat_id):
            raise HTTPException(404, "No such conversation.")
    return {"ok": True}


@app.get("/api/coach/memory")
def memory_list(user: dict = Depends(security.current_user)):
    return {"memories": db.memories(user["id"]), "limit": store_mod.MAX_MEMORIES,
            "max_chars": store_mod.MAX_MEMORY_CHARS}


@app.post("/api/coach/memory")
def memory_add(payload: dict[str, Any] = Body(...),
               user: dict = Depends(security.current_user)):
    source = payload.get("chat_id")
    if source is not None:
        source = _int_id(source)
        if not db.get_chat(user["id"], source):
            source = None
    try:
        return db.add_memory(user["id"], str(payload.get("text") or ""), source)
    except db.MemoryFull:
        raise HTTPException(409, "The coach's memory is full. Delete a fact first.") from None
    except ValueError:
        raise HTTPException(400, "Write something to remember.") from None


@app.patch("/api/coach/memory/{memory_id}")
def memory_update(memory_id: int, payload: dict[str, Any] = Body(...),
                  user: dict = Depends(security.current_user)):
    try:
        with db.store() as handle:
            found = handle.update_memory(user["id"], memory_id,
                                         str(payload.get("text") or ""))
    except ValueError:
        raise HTTPException(400, "Write something to remember.") from None
    if not found:
        raise HTTPException(404, "No such memory.")
    return found


@app.delete("/api/coach/memory/{memory_id}")
def memory_delete(memory_id: int, user: dict = Depends(security.current_user)):
    with db.store() as handle:
        if not handle.delete_memory(user["id"], memory_id):
            raise HTTPException(404, "No such memory.")
    return {"ok": True}


# ---- ingest -----------------------------------------------------------------
@app.post("/api/ingest")
def push(payload: dict[str, Any] = Body(...),
         user: dict = Depends(security.user_from_sync_token)):
    """Receive a bundle from a local sync or from the browser extension.

    The local sync sends finished summaries; the extension sends Garmin's raw
    answers under `results` and this end does the summarizing. Both paths land
    in the same rows.
    """
    if not isinstance(payload, dict):
        return JSONResponse({"error": "Expected a JSON object."}, 400)

    results = payload.get("results")
    if isinstance(results, dict):
        payload = {**payload, "raw": garmin_fetch.regroup(results)}

    for key in ("activities", "days"):
        if key in payload and not isinstance(payload[key], list):
            return JSONResponse({"error": f"{key} must be a list"}, 400)

    activities, days, meta = ingest.normalise(payload)
    stored = db.ingest(user["id"], activities, days, meta)
    failures = 0
    if isinstance(results, dict):
        failures = sum(
            1 for value in results.values()
            if isinstance(value, dict) and "__error" in value)
    info = {
        "at": date.today().isoformat(),
        "stored": stored,
        "failures": failures,
        "incoming_activities": len(activities),
        "incoming_days": len(days),
    }
    with db.store() as handle:
        handle.set_meta(user["id"], "last_ingest", info)
        handle.commit()
    print(
        f"[garmin-sync] ingest user={user.get('email')} "
        f"+{stored.get('activities', 0)} activities "
        f"+{stored.get('days', 0)} days "
        f"failures={failures}",
        flush=True,
    )
    status = db.status(user["id"])
    body = {"ok": True, "stored": stored, "failures": failures, **status}
    sent_anything = bool(results) or bool(payload.get("raw")) or bool(
        payload.get("activities") or payload.get("days"))
    if (sent_anything and not status.get("activities") and not status.get("days")):
        body["ok"] = False
        body["error"] = (
            "Garmin answered but nothing usable was stored. Keep "
            "connect.garmin.com open and signed in, then Sync now again. "
            "Use the same account the extension is connected to."
        )
        return JSONResponse(body, 422)
    return body


# ---- the browser extension --------------------------------------------------
# Authenticated by sync token, like ingest: the extension runs unattended and
# has no session cookie for this site.
@app.get("/api/sync/state")
def sync_state(user: dict = Depends(security.user_from_sync_token)):
    """What the extension needs to decide how much to fetch."""
    info = db.status(user["id"])
    return {
        "email": user["email"],
        "last": info.get("last"),
        "first": info.get("first"),
        "activities": info.get("activities"),
        "days": info.get("days"),
    }


@app.post("/api/sync/fetchplan")
def sync_fetchplan(payload: dict[str, Any] = Body(...),
                   user: dict = Depends(security.user_from_sync_token)):
    """Given the Garmin profile, answer with everything worth fetching.

    The extension has no idea which endpoints exist or which dates are
    missing; it just walks the list this returns.
    """
    profile = payload.get("profile")
    pages = payload.get("pages")
    pages = pages if isinstance(pages, int) and 0 <= pages <= 80 else 1
    limit = payload.get("days")
    limit = (limit if isinstance(limit, int) and 1 <= limit <= 400
             else garmin_fetch.FIRST_RUN_DAYS)
    activity_start = payload.get("activity_start")
    activity_start = (activity_start if isinstance(activity_start, int)
                      and activity_start >= 0 else 0)

    info = db.status(user["id"])
    backfill = bool(payload.get("backfill"))
    before = info.get("first") if backfill else None
    explicit = payload.get("before")
    if isinstance(explicit, str) and len(explicit) >= 10:
        before = explicit[:10]
        backfill = True

    try:
        if backfill and before:
            plan = garmin_fetch.build_plan(
                profile, backfill_before=before, pages=pages, limit=limit,
                activity_start=activity_start,
                include_meta=bool(payload.get("meta")))
        else:
            last = None if payload.get("full") else info.get("last")
            plan = garmin_fetch.build_plan(
                profile, last=last,
                pages=max(pages, garmin_fetch.INCREMENTAL_ACTIVITY_PAGES),
                limit=limit, activity_start=activity_start, include_meta=True)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, 400)
    return plan


@app.get("/api/sync/outbox")
def sync_outbox(user: dict = Depends(security.user_from_sync_token)):
    """Garmin write operations waiting for the extension."""
    from . import planning
    return {"ops": planning.outbox_ops(user["id"])}


@app.post("/api/sync/ack")
def sync_ack(payload: dict[str, Any] = Body(...),
             user: dict = Depends(security.user_from_sync_token)):
    from . import planning
    results = payload.get("results") or payload.get("acks") or []
    if not isinstance(results, list):
        return JSONResponse({"error": "results must be a list"}, 400)
    return planning.ack_ops(user["id"], results)


# ---- direct Garmin connection -----------------------------------------------
# Signed in with the site's own cookie. The Garmin password passes through
# once and is never stored; see garmin_connect.py.
def _garmin_error(error: garmin_connect.GarminError, status: int = 400):
    return JSONResponse({"error": str(error)}, status)


@app.get("/api/garmin")
def garmin_status(user: dict = Depends(security.current_user)):
    return garmin_connect.connection(user["id"])


@app.post("/api/garmin/connect")
def garmin_connect_start(request: Request,
                         email: str = Body("", embed=True),
                         password: str = Body("", embed=True),
                         user: dict = Depends(security.current_user)):
    if security.locked_out(request):
        return JSONResponse(
            {"error": "Too many attempts. Wait a few minutes and try again."}, 429)
    try:
        result = garmin_connect.start_login(user["id"], email, password)
    except garmin_connect.GarminError as e:
        security.record_failure(request)
        return _garmin_error(e)
    return {**result, **garmin_connect.connection(user["id"])}


@app.post("/api/garmin/mfa")
def garmin_connect_mfa(request: Request,
                       challenge_id: str = Body("", embed=True),
                       code: str = Body("", embed=True),
                       user: dict = Depends(security.current_user)):
    if security.locked_out(request):
        return JSONResponse(
            {"error": "Too many attempts. Wait a few minutes and try again."}, 429)
    try:
        result = garmin_connect.finish_mfa(user["id"], challenge_id, code)
    except garmin_connect.GarminError as e:
        security.record_failure(request)
        return _garmin_error(e)
    return {**result, **garmin_connect.connection(user["id"])}


@app.delete("/api/garmin/connect")
def garmin_disconnect(user: dict = Depends(security.current_user)):
    garmin_connect.disconnect(user["id"])
    return garmin_connect.connection(user["id"])


@app.post("/api/garmin/sync")
def garmin_sync_now(history: bool = Body(False, embed=True),
                    user: dict = Depends(security.current_user)):
    info = garmin_connect.connection(user["id"])
    if not info.get("connected"):
        return JSONResponse({"error": "Garmin isn't connected."}, 400)
    rounds = (garmin_connect.HISTORY_ROUNDS_FIRST if history
              else garmin_connect.HISTORY_ROUNDS_TICK)
    started = garmin_connect.kick(user["id"], history_rounds=rounds)
    return {**garmin_connect.connection(user["id"]), "started": started,
            "running": True}


# ---- importing Garmin's own export -----------------------------------------
@app.post("/api/import/garmin-export")
async def import_garmin_export(request: Request,
                               user: dict = Depends(security.current_user)):
    """Load a full history from the zip Garmin emails you on request.

    The whole point is that this needs nothing installed: request the export
    from your Garmin account, drop the zip here, and years of history land in
    one go. Streamed to a temporary file rather than held in memory, because
    these archives run to hundreds of megabytes.
    """
    import tempfile
    from pathlib import Path

    size = 0
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        try:
            async for chunk in request.stream():
                size += len(chunk)
                if size > garmin_export.MAX_UPLOAD_BYTES:
                    return JSONResponse(
                        {"error": "That file is larger than "
                                  f"{garmin_export.MAX_UPLOAD_BYTES // (1024 * 1024)} MB."},
                        413)
                tmp.write(chunk)
        finally:
            tmp.close()

        if not size:
            return JSONResponse({"error": "No file arrived."}, 400)
        try:
            found = await run_in_threadpool(garmin_export.read_archive, Path(tmp.name))
        except garmin_export.NotAnExport as e:
            return JSONResponse({"error": str(e)}, 400)
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    stored = await run_in_threadpool(db.ingest, user["id"], found.activities, found.days)
    return {"ok": True, "stored": stored, "report": found.report,
            **(await run_in_threadpool(db.status, user["id"]))}


@app.get("/api/meta")
def garmin_meta(user: dict = Depends(security.current_user)):
    """Zones, personal records and Garmin's own race predictions."""
    return db.meta(user["id"])


@app.get("/extension.zip", include_in_schema=False)
def extension_zip():
    """A downloadable copy of the browser extension, for Load unpacked."""
    import io
    import zipfile

    root = config.ROOT / "extension"
    if not root.is_dir():
        raise HTTPException(404, "Extension pack is missing from this deploy.")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.name != ".DS_Store":
                zf.write(path, f"garmin-coach-extension/{path.relative_to(root).as_posix()}")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition":
                 'attachment; filename="garmin-coach-extension.zip"'},
    )
