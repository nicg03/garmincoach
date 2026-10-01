"""
The direct Garmin connection: the site fetches for you, no extension needed.

A user types their Garmin email and password once (plus a code if Garmin
asks for one). `garminconnect` turns that into OAuth tokens the same way the
Garmin mobile app does; the password is dropped straight away and only the
tokens are kept, encrypted. From then on the server runs the exact fetch plan
the extension runs (`garmin_fetch.build_plan`), through the same summarizer
(`ingest.normalise`), and writes queued workouts from the same outbox. The
extension and the export zip keep working as other ways into the same rows.

Two things are held in memory rather than the database, and both are fine
with the single container `railway.json` pins:

  - a login waiting for its MFA code: garminconnect keeps that state on the
    client object, so the second request has to find the same object
  - a per-user lock, so the scheduler, a "Sync now" click and a page load
    never run two syncs of one account at once
"""
from __future__ import annotations

import asyncio
import random
import re
import secrets
import threading
import time
from typing import Any
from urllib.parse import parse_qsl

from garmin_sync import endpoints as ep
from garmin_sync import garmin_workout

from . import config, db, garmin_fetch, ingest

MFA_TTL_SECONDS = 300

# Politeness towards Garmin, as in the extension: a sync is a couple of
# hundred small requests and they go one after another with a short gap.
REQUEST_PAUSE_SECONDS = 0.15
ACTIVITY_LIST_RETRIES = 3
ACTIVITY_LIST_RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
ACTIVITY_LIST_FAILED_MSG = (
    "Garmin didn't return your recent activities. The next sync will retry.")

# Specs per ingest, so a long sync stores as it goes.
CHUNK_SIZE = 24

# A page load refreshes the data when the last sync is older than this.
STALE_ON_OPEN_SECONDS = 3600

# History rounds per run. A background run after connecting walks far; the
# scheduler nibbles so no single tick holds Garmin for minutes.
HISTORY_ROUNDS_FIRST = 40
HISTORY_ROUNDS_TICK = 3
HISTORY_PAGES = 8
HISTORY_DAYS = 45

SCHEDULER_TICK_SECONDS = 600


class GarminError(Exception):
    """Something the user should read, already phrased for them."""


class SessionExpired(GarminError):
    """Garmin stopped accepting the stored tokens; a fresh sign-in is needed."""


_pending: dict[str, dict] = {}
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()
_running: set[int] = set()


def _log(message: str) -> None:
    print(f"[garmin-sync] direct {message}", flush=True)


def available() -> bool:
    try:
        import garminconnect  # noqa: F401
    except ImportError:
        return False
    return True


def _garmin_class():
    try:
        from garminconnect import Garmin
    except ImportError as e:
        raise GarminError(
            "The direct Garmin connection isn't installed on this server.") from e
    return Garmin


def _lock(user_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(user_id, threading.Lock())


# ---- tokens at rest ---------------------------------------------------------
def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(db.garmin_token_key().encode())


def encrypt(tokens: str) -> str:
    return _fernet().encrypt(tokens.encode()).decode()


def decrypt(blob: str) -> str:
    from cryptography.fernet import InvalidToken

    try:
        return _fernet().decrypt(blob.encode()).decode()
    except InvalidToken as e:
        raise SessionExpired(
            "The stored Garmin connection can't be read any more (the "
            "encryption key changed). Connect Garmin again.") from e


# ---- talking to Garmin ------------------------------------------------------
_STATUS = re.compile(r"\b([1-5]\d\d)\b")


def _status_of(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    match = _STATUS.search(str(error))
    return int(match.group(1)) if match else None


def _split_query(path: str) -> tuple[str, dict[str, str]]:
    """Separate a gc-api path from its query string for garminconnect/garth."""
    if "?" not in path:
        return path, {}
    path_only, query = path.split("?", 1)
    return path_only, {k: v for k, v in parse_qsl(query, keep_blank_values=True)}


def _is_activity_list(path: str) -> bool:
    return path.startswith("/activitylist-service/")


class Session:
    """A signed-in Garmin client, answering like the extension's content
    script does: JSON on success, an `__error` marker on a failed endpoint,
    and an exception only when the whole session is gone."""

    def __init__(self, client: Any):
        self.client = client

    @classmethod
    def from_tokens(cls, tokens: str) -> "Session":
        api = _garmin_class()()
        try:
            api.client.loads(tokens)
        except Exception as e:  # noqa: BLE001
            raise SessionExpired("The Garmin connection needs signing in again.") from e
        return cls(api.client)

    def dumps(self) -> str:
        return self.client.dumps()

    def _guard(self, error: Exception) -> dict:
        status = _status_of(error)
        name = type(error).__name__
        if status == 401 or name == "GarminConnectAuthenticationError":
            raise SessionExpired(
                "Garmin asks to sign in again. Reconnect Garmin to keep "
                "syncing.") from error
        if status == 429 or name == "GarminConnectTooManyRequestsError":
            raise GarminError(
                "Garmin is rate limiting this server. The next sync will "
                "pick up where this one stopped.") from error
        return {"__error": status or "fetch-failed", "__detail": str(error)[:200]}

    def _retryable(self, error: Exception) -> bool:
        status = _status_of(error)
        name = type(error).__name__
        return (status in ACTIVITY_LIST_RETRY_STATUSES
                or name == "GarminConnectTooManyRequestsError")

    def get(self, path: str) -> Any:
        path_only, params = _split_query(path)
        retries = ACTIVITY_LIST_RETRIES if _is_activity_list(path_only) else 0
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                kwargs = {"params": params} if params else {}
                result = self.client.connectapi(path_only, **kwargs)
                if result is None and _is_activity_list(path_only):
                    return []
                return result
            except Exception as e:  # noqa: BLE001
                last_error = e
                if _is_activity_list(path_only) and attempt < retries and self._retryable(e):
                    time.sleep(REQUEST_PAUSE_SECONDS * (attempt + 2))
                    continue
                return self._guard(e)
            finally:
                time.sleep(REQUEST_PAUSE_SECONDS)
        return self._guard(last_error) if last_error else {"__error": "fetch-failed"}

    def send(self, method: str, path: str, body: Any) -> dict:
        kwargs = {} if body is None else {"json": body}
        try:
            response = self.client.request(method or "POST", "connectapi", path, **kwargs)
        except Exception as e:  # noqa: BLE001
            return self._guard(e)
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            payload = {}
        return payload if isinstance(payload, dict) else {"result": payload}


# ---- connecting -------------------------------------------------------------
def _purge_pending() -> None:
    now = time.time()
    for key in [k for k, v in _pending.items() if v["expires"] < now]:
        _pending.pop(key, None)


def _login_error(error: Exception) -> GarminError:
    name = type(error).__name__
    if name == "GarminConnectAuthenticationError":
        return GarminError("Garmin didn't accept that email and password.")
    if name == "GarminConnectTooManyRequestsError" or _status_of(error) == 429:
        return GarminError(
            "Garmin is refusing sign-ins from this server right now. Wait a "
            "few minutes and try again, or use the extension instead.")
    return GarminError(
        "Couldn't reach Garmin's sign-in from this server. Try again, or use "
        "the extension instead.")


def start_login(user_id: int, email: str, password: str) -> dict:
    """First step. Either connected, or waiting for the MFA code."""
    email = (email or "").strip()
    if not email or not password:
        raise GarminError("Enter your Garmin email and password.")
    Garmin = _garmin_class()
    api = Garmin(email, password, return_on_mfa=True)
    try:
        status, _ = api.login()
    except Exception as e:  # noqa: BLE001
        _log(f"login failed user={user_id}: {type(e).__name__}: {e}")
        raise _login_error(e) from e
    if status == "needs_mfa":
        _purge_pending()
        challenge = secrets.token_urlsafe(16)
        _pending[challenge] = {
            "api": api, "user_id": user_id, "email": email,
            "expires": time.time() + MFA_TTL_SECONDS,
        }
        return {"status": "mfa", "challenge_id": challenge}
    _connected(user_id, email, api.client)
    return {"status": "connected"}


def finish_mfa(user_id: int, challenge_id: str, code: str) -> dict:
    _purge_pending()
    pending = _pending.pop(challenge_id or "", None)
    if not pending or pending["user_id"] != user_id:
        raise GarminError("That sign-in expired. Start again.")
    code = re.sub(r"\s", "", code or "")
    if not code:
        raise GarminError("Enter the code Garmin sent you.")
    try:
        pending["api"].resume_login(None, code)
    except Exception as e:  # noqa: BLE001
        _log(f"mfa failed user={user_id}: {type(e).__name__}: {e}")
        raise GarminError("Garmin didn't accept that code. Start again.") from e
    _connected(user_id, pending["email"], pending["api"].client)
    return {"status": "connected"}


def _connected(user_id: int, email: str, client: Any) -> None:
    with db.store() as handle:
        handle.save_garmin_account(user_id, email, encrypt(client.dumps()))
    _log(f"connected user={user_id}")
    kick(user_id, history_rounds=HISTORY_ROUNDS_FIRST)


def disconnect(user_id: int) -> None:
    with db.store() as handle:
        handle.delete_garmin_account(user_id)


def connection(user_id: int) -> dict:
    """What the site shows about the direct connection."""
    with db.store() as handle:
        account = handle.garmin_account(user_id)
    if not account:
        return {"available": available(), "connected": False}
    backfill = account.get("backfill") or {}
    return {
        "available": available(),
        "connected": True,
        "garmin_email": account["garmin_email"],
        "since": account["created"],
        "last_sync": account["last_sync"],
        "last_result": account["last_result"],
        "last_error": account["last_error"],
        "needs_login": bool(account["needs_login"]),
        "running": user_id in _running,
        "history_done": bool(backfill.get("done")),
    }


# ---- syncing ----------------------------------------------------------------
def _chunks(specs: list[dict]) -> list[list[dict]]:
    """Same split as the extension: never cut one day's labels in two, so a
    later ingest cannot overwrite a day with a partial row."""
    out: list[list[dict]] = []
    current: list[dict] = []
    current_day = None
    for spec in specs:
        parts = str(spec.get("label") or "").split("::")
        day = parts[1] if len(parts) > 1 else ""
        if len(current) >= CHUNK_SIZE and day != current_day:
            out.append(current)
            current = []
        current.append(spec)
        current_day = day
    if current:
        out.append(current)
    return out


def _activity_count(results: dict) -> int:
    return sum(len(garmin_fetch._activity_items(v)) for k, v in results.items()
               if k.startswith("activities::"))


def _activity_list_failed(results: dict) -> bool:
    return any(k.startswith("activities::") and isinstance(v, dict) and "__error" in v
               for k, v in results.items())


def _empty_totals() -> dict:
    return {"activities": 0, "days": 0, "failures": 0, "fetched_activities": 0,
            "activity_list_failed": False}


def _add_totals(into: dict, extra: dict) -> dict:
    into["activity_list_failed"] = bool(
        into.get("activity_list_failed") or extra.get("activity_list_failed"))
    for key in ("activities", "days", "failures", "fetched_activities"):
        into[key] = into.get(key, 0) + extra.get(key, 0)
    return into


def _fetch_and_store(user_id: int, session: Session, specs: list[dict]) -> dict:
    totals = _empty_totals()
    for chunk in _chunks(specs):
        results = {spec["label"]: session.get(spec["path"]) for spec in chunk}
        totals["failures"] += sum(
            1 for v in results.values() if isinstance(v, dict) and "__error" in v)
        totals["fetched_activities"] += _activity_count(results)
        totals["activity_list_failed"] = (
            totals["activity_list_failed"] or _activity_list_failed(results))
        activities, days, meta = ingest.normalise(
            {"raw": garmin_fetch.regroup(results)})
        stored = db.ingest(user_id, activities, days, meta)
        totals["activities"] += stored.get("activities", 0)
        totals["days"] += stored.get("days", 0)
    return totals


def _profile(session: Session) -> dict:
    profile = session.get(ep.PROFILE)
    if not isinstance(profile, dict) or "__error" in profile:
        raise GarminError("Garmin didn't return your profile. Try again later.")
    return profile


def _incremental(user_id: int, session: Session, profile: dict) -> dict:
    last = db.status(user_id).get("last")
    pages = garmin_fetch.INCREMENTAL_ACTIVITY_PAGES
    plan = garmin_fetch.build_plan(
        profile, last=last, pages=pages,
        limit=garmin_fetch.MAX_DAYS_PER_SYNC if last else garmin_fetch.FIRST_RUN_DAYS)
    totals = _fetch_and_store(user_id, session, plan["specs"])
    page_size = garmin_fetch.ACTIVITIES_PER_PAGE
    extra = 0
    offset = plan["activity_start"] + pages * page_size
    while (not totals["activity_list_failed"]
           and totals["fetched_activities"] >= (pages + extra) * page_size
           and extra < garmin_fetch.EXTRA_ACTIVITY_PAGES):
        more = garmin_fetch.activity_specs(
            offset, 1,
            start_date=plan.get("activity_start_date"),
            end_date=plan.get("activity_end_date"))
        got = _fetch_and_store(user_id, session, more)
        _add_totals(totals, got)
        extra += 1
        offset += page_size
        if got["activity_list_failed"] or got["fetched_activities"] < page_size:
            break
    return totals


def _history(user_id: int, session: Session, profile: dict,
             cursor: dict | None, rounds: int) -> dict:
    """Walk further into the past, like the extension's Load all history."""
    cursor = dict(cursor or {})
    cursor.setdefault("activity_start", 0)
    cursor.setdefault("activities_done", False)
    added = {"activities": 0, "days": 0}
    for _ in range(max(0, rounds)):
        if cursor.get("done"):
            break
        first = db.status(user_id).get("first")
        pages = 0 if cursor["activities_done"] else HISTORY_PAGES
        if not first:
            cursor["done"] = True
            break
        plan = garmin_fetch.build_plan(
            profile, backfill_before=first, pages=pages, limit=HISTORY_DAYS,
            activity_start=cursor["activity_start"], include_meta=False)
        if not plan["specs"]:
            cursor["done"] = True
            break
        got = _fetch_and_store(user_id, session, plan["specs"])
        added["activities"] += got["activities"]
        added["days"] += got["days"]
        if pages:
            if got.get("activity_list_failed"):
                # Same offset next round; a short page caused by errors is not
                # the end of the list.
                pass
            elif got["fetched_activities"] < pages * garmin_fetch.ACTIVITIES_PER_PAGE:
                cursor["activities_done"] = True
            else:
                cursor["activity_start"] += got["fetched_activities"]
        if not plan["days"] and cursor["activities_done"]:
            cursor["done"] = True
    return {**added, "cursor": cursor}


def _apply_outbox(user_id: int, session: Session) -> int:
    """Queued workouts to Garmin, the same ops the extension writes."""
    from . import planning

    ops = planning.outbox_ops(user_id)
    workout_ids: dict[str, int] = {}
    acks: list[dict] = []
    for op in ops:
        path = op.get("path")
        if op.get("ref") == "schedule" and not path:
            workout_id = workout_ids.get(op["session_id"])
            if not workout_id:
                acks.append({"session_id": op["session_id"],
                             "error": "No workout id to schedule"})
                continue
            path = garmin_workout.schedule_op(workout_id, op["body"]["date"])["path"]
        result = session.send(op.get("method"), path, op.get("body"))
        ack: dict = {"session_id": op["session_id"]}
        if "__error" in result:
            ack["error"] = str(result.get("__detail") or result["__error"])
        elif op.get("ref") == "workout":
            workout_id = result.get("workoutId")
            if workout_id:
                workout_ids[op["session_id"]] = workout_id
                ack["garmin_workout_id"] = workout_id
        elif op.get("ref") == "schedule" and result.get("workoutScheduleId"):
            ack["garmin_schedule_id"] = result["workoutScheduleId"]
        acks.append(ack)
    if acks:
        planning.ack_ops(user_id, acks)
    return len(acks)


def sync(user_id: int, history_rounds: int = HISTORY_ROUNDS_TICK) -> dict:
    """One full run: new days, queued workouts, then a bite of history."""
    lock = _lock(user_id)
    if not lock.acquire(blocking=False):
        return {"state": "running"}
    _running.add(user_id)
    try:
        with db.store() as handle:
            account = handle.garmin_account(user_id)
        if not account:
            raise GarminError("Garmin isn't connected.")
        started = time.time()
        tokens = session = None
        try:
            tokens = decrypt(account["tokens"])
            session = Session.from_tokens(tokens)
            profile = _profile(session)
            result = _incremental(user_id, session, profile)
            result["written"] = _apply_outbox(user_id, session)
            history = _history(user_id, session, profile,
                               account.get("backfill"), history_rounds)
            result["history"] = {k: history[k] for k in ("activities", "days")}
            backfill = history["cursor"]
        except SessionExpired as e:
            with db.store() as handle:
                handle.update_garmin_account(user_id, needs_login=1, last_error=str(e))
            raise
        except GarminError as e:
            with db.store() as handle:
                handle.update_garmin_account(user_id, last_error=str(e))
            raise
        finally:
            fresh = session.dumps() if session else tokens
            if fresh != tokens:
                with db.store() as handle:
                    handle.update_garmin_account(user_id, tokens=encrypt(fresh))

        result["seconds"] = round(time.time() - started, 1)
        list_failed = bool(result.get("activity_list_failed"))
        with db.store() as handle:
            fields = {"last_result": result, "backfill": backfill}
            if list_failed:
                fields["last_error"] = ACTIVITY_LIST_FAILED_MSG
            else:
                fields["last_sync"] = time.time()
                fields["last_error"] = None
            handle.update_garmin_account(user_id, **fields)
            if not list_failed:
                handle.set_meta(user_id, "last_ingest", {
                    "at": time.strftime("%Y-%m-%d"),
                    "source": "garmin",
                    "stored": {"activities": result["activities"],
                               "days": result["days"]},
                    "failures": result["failures"],
                })
            handle.commit()
        _log(f"sync user={user_id} +{result['activities']} activities "
             f"+{result['days']} days failures={result['failures']} "
             f"list_failed={list_failed} in {result['seconds']}s")
        return result
    finally:
        _running.discard(user_id)
        lock.release()


def _safe_sync(user_id: int, history_rounds: int) -> None:
    try:
        sync(user_id, history_rounds=history_rounds)
    except GarminError as e:
        _log(f"sync user={user_id} stopped: {e}")
    except Exception as e:  # noqa: BLE001
        _log(f"sync user={user_id} crashed: {type(e).__name__}: {e}")
        with db.store() as handle:
            handle.update_garmin_account(
                user_id, last_error="The last sync failed unexpectedly. It will retry.")


def kick(user_id: int, history_rounds: int = HISTORY_ROUNDS_TICK) -> bool:
    """Start a sync in the background. False if one is already running."""
    with _locks_guard:
        if user_id in _running:
            return False
        _running.add(user_id)
    threading.Thread(target=_safe_sync, args=(user_id, history_rounds),
                     daemon=True, name=f"garmin-sync-{user_id}").start()
    return True


def refresh_if_stale(user_id: int) -> None:
    """Called when someone opens the site: an hour-old sync gets redone."""
    with db.store() as handle:
        account = handle.garmin_account(user_id)
    if not account or account["needs_login"]:
        return
    if account.get("last_error") or (
            time.time() - (account["last_sync"] or 0) > STALE_ON_OPEN_SECONDS):
        kick(user_id)


def due_users(now: float | None = None) -> list[int]:
    now = now or time.time()
    interval = config.GARMIN_SYNC_HOURS * 3600
    with db.store() as handle:
        rows = handle.garmin_connected_users()
    return [r["user_id"] for r in rows
            if not r["needs_login"] and (
                r.get("last_error")
                or now - (r["last_sync"] or 0) >= interval)]


async def scheduler() -> None:
    """Every connected account, every few hours, one at a time with jitter."""
    await asyncio.sleep(30)
    while True:
        try:
            for user_id in due_users():
                await asyncio.to_thread(_safe_sync, user_id, HISTORY_ROUNDS_TICK)
                await asyncio.sleep(random.uniform(5, 20))
        except Exception as e:  # noqa: BLE001
            _log(f"scheduler error: {type(e).__name__}: {e}")
        await asyncio.sleep(SCHEDULER_TICK_SECONDS + random.uniform(0, 60))
