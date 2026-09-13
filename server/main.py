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
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from garmin_sync import metrics

from . import coach, config, db, garmin_export, garmin_fetch, ingest, security
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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
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


@app.get("/styles.css", include_in_schema=False)
@app.get("/app.js", include_in_schema=False)
def legacy_static(request: Request):
    name = request.url.path.rsplit("/", 1)[-1]
    return FileResponse(STATIC_DIR / name)


@app.get("/api/config")
def site_config():
    """What the page needs before anyone has signed in."""
    return {
        "signup_open": config.SIGNUP_OPEN,
        "coach": config.coach_enabled(),
        "min_password": config.MIN_PASSWORD,
        "repo": config.SYNC_REPO_URL,
        "extension_zip": "/extension.zip",
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


# ---- performance, plans, workouts ------------------------------------------
from . import planning


@app.get("/api/performance")
def performance_view(user: dict = Depends(security.current_user)):
    from garmin_sync.performance import build as build_perf
    _days, activities = db.window(user["id"], None, None)
    return build_perf(activities, db.meta(user["id"]))


@app.get("/api/athlete")
def get_athlete(user: dict = Depends(security.current_user)):
    return planning.athlete(user["id"])


@app.put("/api/athlete")
def put_athlete(payload: dict[str, Any] = Body(...),
                user: dict = Depends(security.current_user)):
    return planning.save_athlete(user["id"], payload)


@app.get("/api/races")
def get_races(user: dict = Depends(security.current_user)):
    return {"races": planning.list_races(user["id"])}


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
             user: dict = Depends(security.current_user)):
    plan = planning.handle_get_plan(user["id"], plan_id)
    if not plan:
        return {"plan": None, "adherence": planning.adherence(user["id"])}
    return {"plan": plan, "adherence": planning.adherence(user["id"])}


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
    from garmin_sync.performance import build as build_perf
    _days, activities = db.window(user["id"], None, None)
    paces = build_perf(activities).get("paces") or {}
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
    """Queue a one-off workout on a date (no full plan required)."""
    from garmin_sync.workout_dsl import validate
    try:
        workout = validate(payload.get("workout") or payload)
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)
    day = (payload.get("date") or date.today().isoformat())[:10]
    session = {
        "id": str(__import__("uuid").uuid4()),
        "date": day,
        "sport": workout["sport"],
        "kind": workout.get("kind") or "easy",
        "workout": workout,
        "est_load": workout.get("est_load"),
        "state": "queued",
        "revision": 1,
    }
    with db.store() as handle:
        plan = handle.active_plan(user["id"])
        plan_id = (plan or {}).get("id") or "adhoc"
        if not plan:
            handle.save_plan(user["id"], {
                "id": plan_id, "status": "active", "race_id": None,
                "created": date.today().isoformat(), "sessions": [session],
            })
        else:
            session["plan_id"] = plan_id
            handle.upsert_session(user["id"], plan_id, session)
            handle.commit()
    return {"session": session}


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
                profile, last=last, pages=max(pages, 1), limit=limit,
                activity_start=activity_start, include_meta=True)
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
            found = garmin_export.read_archive(Path(tmp.name))
        except garmin_export.NotAnExport as e:
            return JSONResponse({"error": str(e)}, 400)
    finally:
        Path(tmp.name).unlink(missing_ok=True)

    stored = db.ingest(user["id"], found.activities, found.days)
    return {"ok": True, "stored": stored, "report": found.report,
            **db.status(user["id"])}


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
