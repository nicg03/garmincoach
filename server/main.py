"""
The FastAPI application.

Routes fall into four groups:

  - the page, signup and sign-in
  - read-only data for the dashboard, scoped to the signed-in account
  - the coach, likewise
  - `POST /api/ingest`, where a Mac pushes summaries, identified by the
    account's own sync token rather than a cookie

Everything reads from SQLite and computes on the fly. There's no background
work and no cache to invalidate: a pull that takes a minute over the network
takes milliseconds to aggregate here.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any
import time

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from garmin_sync import metrics

from . import coach, config, db, security
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
    if config.coach_enabled():
        notes.append(f"coach: on ({config.coach_provider()} / {config.coach_model()})")
    else:
        notes.append("coach: off (set OPENAI_API_KEY or ANTHROPIC_API_KEY)")
    if config.coach_enabled() and config.COACH_DAILY_LIMIT:
        notes.append(f"coach limit: {config.COACH_DAILY_LIMIT} calls per user per day")
    return notes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    for note in startup_notes():
        print(f"[garmin-sync] {note}", flush=True)
    yield


app = FastAPI(title="Garmin Sync", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _range(days: int | None, frm: str | None, to: str | None) -> tuple[str, str]:
    """Resolve the requested window into a concrete (start, end) pair."""
    end = to or date.today().isoformat()
    if frm:
        return frm, end
    span = days or config.DEFAULT_WINDOW_DAYS
    start = (date.fromisoformat(end) - timedelta(days=span - 1)).isoformat()
    return start, end


def _public_user(user: dict) -> dict:
    return {
        "email": user["email"],
        "since": user["created"],
        "sync_token": user["sync_token"],
    }


# ---- page and accounts ------------------------------------------------------
@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def site_config():
    """What the page needs before anyone has signed in."""
    return {
        "signup_open": config.SIGNUP_OPEN,
        "coach": config.coach_enabled(),
        "min_password": config.MIN_PASSWORD,
        "repo": config.SYNC_REPO_URL,
    }


@app.post("/api/signup")
def signup(request: Request, response: Response,
           email: str = Body("", embed=True),
           password: str = Body("", embed=True)):
    if not config.SIGNUP_OPEN:
        return JSONResponse({"error": "Signups are closed right now."}, 403)
    if security.locked_out(request):
        return JSONResponse({"error": "Too many attempts. Try again later."}, 429)

    problem = security.email_problem(email) or security.password_problem(password)
    if problem:
        return JSONResponse({"error": problem}, 400)

    try:
        with db.store() as handle:
            user = handle.create_user(email, security.hash_password(password))
    except EmailTaken:
        # Deliberately explicit: this is a personal tool, not a service where
        # hiding which emails exist buys anything.
        return JSONResponse({"error": "That email already has an account."}, 409)

    response.set_cookie(security.COOKIE_NAME, security.issue_session(user["id"]),
                        **security.cookie_kwargs(request))
    return {"ok": True, "user": _public_user(user)}


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
    response.set_cookie(security.COOKIE_NAME, security.issue_session(user["id"]),
                        **security.cookie_kwargs(request))
    return {"ok": True, "user": _public_user(user)}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(security.COOKIE_NAME, path="/")
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(security.current_user)):
    return _public_user(user)


@app.post("/api/token/rotate")
def rotate_token(user: dict = Depends(security.current_user)):
    """Invalidates the old token immediately, so a leaked one stops working."""
    with db.store() as handle:
        token = handle.rotate_token(user["id"])
    return {"sync_token": token}


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
def delete_account(response: Response, confirm: str = Body("", embed=True),
                   user: dict = Depends(security.current_user)):
    if confirm.strip().lower() != user["email"]:
        return JSONResponse(
            {"error": "Type your email address to confirm."}, 400)
    with db.store() as handle:
        handle.delete_user(user["id"])
    response.delete_cookie(security.COOKIE_NAME, path="/")
    return {"ok": True}


# ---- data -------------------------------------------------------------------
@app.get("/api/status")
def status(user: dict = Depends(security.current_user)):
    info = db.status(user["id"])
    last = info.get("last")
    if last:
        gap = (date.today() - date.fromisoformat(last)).days
        info["ago"] = "today" if gap == 0 else ("yesterday" if gap == 1
                                                else f"{gap} days ago")
        info["stale"] = gap > 2
    else:
        info["ago"] = ""
        info["stale"] = True
    info["coach"] = config.coach_enabled()
    info["coach_limit"] = config.COACH_DAILY_LIMIT
    info["coach_used"] = (db.coach_calls_today(user["id"])
                          if config.COACH_DAILY_LIMIT else 0)
    info["user"] = _public_user(user)
    info["repo"] = config.SYNC_REPO_URL
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
                      user: dict = Depends(security.current_user)):
    """Aggregated series for the charts.

    Reads a warmup period before the visible window so the smoothed curves
    start where they belong, then trims it back off.
    """
    start, end = _range(days, frm, to)
    # Asking for a year when you only have a month of history would otherwise
    # draw eleven empty months, so the window shrinks to the data you have.
    # The warmup is clamped the same way: padding with days that predate the
    # history would seed the smoothed curves with rest days that never
    # happened, and the coach -- which reads from the first stored day -- would
    # then quote different numbers than the charts.
    first = db.status(user["id"]).get("first")
    if first and first > start:
        start = first
    warmup = (date.fromisoformat(start) - timedelta(days=WARMUP_DAYS)).isoformat()
    if first and warmup < first:
        warmup = first

    day_rows, activity_rows = db.window(user["id"], warmup, end)
    payload = metrics.build(day_rows, activity_rows, start=warmup, end=end,
                            visible_from=start)
    payload["window"] = {"from": start, "to": end}
    payload["recent_activities"] = list(reversed(activity_rows))[:25]
    return payload


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
def brief(refresh: int = 0, user: dict = Depends(security.current_user)):
    """Today's briefing, generated once and cached for the rest of the day."""
    cached = db.get_briefing(user["id"], date.today().isoformat())
    if cached and not refresh:
        return {"date": date.today().isoformat(), "text": cached, "cached": True}

    _check_coach(user)
    try:
        result = coach.cached_briefing(user["id"], refresh=bool(refresh))
    except Exception as e:  # noqa: BLE001 - surface the provider's complaint
        raise HTTPException(502, f"The coach couldn't answer: {e}") from None
    db.record_coach_call(user["id"])
    return result


@app.post("/api/chat")
def chat(payload: dict[str, Any] = Body(...),
         user: dict = Depends(security.current_user)):
    _check_coach(user)
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "Ask something.")
    history = payload.get("history")
    db.record_coach_call(user["id"])
    return StreamingResponse(
        coach.stream(user["id"], question[:2000],
                     history if isinstance(history, list) else None),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---- ingest -----------------------------------------------------------------
@app.post("/api/ingest")
def ingest(payload: dict[str, Any] = Body(...),
           user: dict = Depends(security.user_from_sync_token)):
    """Receive a bundle pushed by someone's local sync."""
    activities_in = payload.get("activities") or []
    days_in = payload.get("days") or []
    if not isinstance(activities_in, list) or not isinstance(days_in, list):
        return JSONResponse({"error": "activities and days must be lists"}, 400)
    stored = db.ingest(user["id"], activities_in, days_in)
    return {"ok": True, "stored": stored, **db.status(user["id"])}
