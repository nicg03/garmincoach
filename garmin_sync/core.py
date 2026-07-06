"""
Programmatic entry points for the desktop app (and reusable by the CLI).

Everything the GUI needs lives here so the interface layer stays thin:
  - pull(...)             fetch a date range, summarize, store, export a run file
  - fill_the_blank(...)   pull from the last stored day up to today
  - export_history(...)   write ONE combined JSON of the entire local history
  - last_pulled_date()    the most recent day we already have
  - db_counts()           how much history is stored

Progress is reported through an optional `log` callback (a function taking a
single string). The GUI passes one that appends to its status pane; the CLI can
pass `print`. Pulls run Playwright's sync API, which is fine inside a worker
thread as long as it's the only Playwright running in that thread.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / ".browser-profile"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "garmin.db"

HISTORY_PATH = DATA_DIR / "garmin_history.json"


def _noop(_msg: str) -> None:
    pass


def last_pulled_date() -> str | None:
    """The most recent day already in the local db (ISO string), or None."""
    from .store import Store

    if not DB_PATH.exists():
        return None
    store = Store(DB_PATH)
    try:
        return store.last_day()
    finally:
        store.close()


def db_counts() -> dict:
    """How many activities/days are stored locally."""
    from .store import Store

    if not DB_PATH.exists():
        return {"activities": 0, "days": 0}
    store = Store(DB_PATH)
    try:
        return store.counts()
    finally:
        store.close()


def export_history(out_path: Path | None = None, log=_noop) -> dict:
    """Write ONE combined JSON with the entire local history (no date filter)."""
    from .store import Store

    out_path = Path(out_path) if out_path else HISTORY_PATH
    DATA_DIR.mkdir(exist_ok=True)
    store = Store(DB_PATH)
    try:
        counts = store.export(out_path, since=None)
    finally:
        store.close()
    log(f"Wrote combined history -> {out_path.name} "
        f"({counts['activities']} activities, {counts['days']} days)")
    return {"path": str(out_path), **counts}


def pull(
    since: date,
    limit: int = 50,
    full: bool = False,
    headless: bool = True,
    log=_noop,
) -> dict:
    """Fetch everything from `since` to today, summarize, store, export a run file.

    Returns a dict with the run file path and counts. Raises on hard auth/API
    failure so the caller can surface it.
    """
    from .auth import browser_session, ensure_logged_in, capture_app_headers
    from .client import GarminClient, GarminAPIError
    from . import summarize as sm
    from .store import Store

    DATA_DIR.mkdir(exist_ok=True)
    today = date.today()
    if since > today:
        since = today
    days = [(today - timedelta(days=i)).isoformat()
            for i in range((today - since).days + 1)]

    store = Store(DB_PATH)
    try:
        log("Opening Garmin session...")
        with browser_session(PROFILE_DIR, headless=headless) as page:
            ensure_logged_in(page)
            app_headers = capture_app_headers(page)
            if "connect-csrf-token" not in app_headers:
                log("Warning: no CSRF token captured; some calls may fail.")
            gc = GarminClient(page, app_headers=app_headers)

            try:
                name = gc.display_name
                log(f"Authenticated as {name}.")
            except GarminAPIError as e:
                raise RuntimeError(
                    f"Couldn't reach the Garmin API ({e}). "
                    "Try logging in again from the app."
                )

            specs = [gc.activities_spec(limit=limit)]
            for d in days:
                specs.extend(gc.day_specs(d))

            log(f"Fetching {len(specs)} endpoints across {len(days)} day(s)...")
            results = gc.batch(specs)

            def ok(v):
                return v if isinstance(v, (list, dict)) and not (
                    isinstance(v, dict) and "__error" in v) else None

            acts = ok(results.get("activities")) or []
            kept = 0
            since_iso = since.isoformat()
            for a in acts:
                start = (a.get("startTimeLocal") or "")[:10]
                if start and start < since_iso:
                    continue
                store.upsert_activity(sm.summarize_activity(a, full=full))
                kept += 1

            for d in days:
                day = sm.build_day(
                    d,
                    daily=ok(results.get(f"daily::{d}")),
                    sleep=ok(results.get(f"sleep::{d}")),
                    hrv=ok(results.get(f"hrv::{d}")),
                    readiness=ok(results.get(f"readiness::{d}")),
                    full=full,
                )
                store.upsert_day(day)

            errs = sorted({str(v["__error"]) for v in results.values()
                           if isinstance(v, dict) and "__error" in v})
            log(f"Stored {kept} activities, {len(days)} day(s)."
                + (f" Some endpoints returned: {', '.join(errs)}." if errs else ""))

        store.commit()
        out_path = DATA_DIR / f"garmin_{today.isoformat()}.json"
        counts = store.export(out_path, since=since_iso)
    finally:
        store.close()

    log(f"Wrote run file -> {out_path.name} "
        f"({counts['activities']} activities, {counts['days']} days)")
    return {"path": str(out_path), **counts}


def fill_the_blank(limit: int = 50, full: bool = False, headless: bool = True,
                   log=_noop) -> dict:
    """Pull from the day after the last stored record up to today.

    If there's no history yet, falls back to the last 14 days.
    """
    last = last_pulled_date()
    today = date.today()
    if last:
        since = date.fromisoformat(last)  # re-pull last day to catch late syncs
        gap = (today - since).days
        log(f"Last data: {last}. Filling {gap} day(s) up to {today.isoformat()}.")
    else:
        since = today - timedelta(days=14)
        log(f"No history yet; pulling the last 14 days ({since.isoformat()} -> {today.isoformat()}).")
    return pull(since=since, limit=limit, full=full, headless=headless, log=log)


def login(fresh: bool = False, log=_noop) -> str:
    """Open a VISIBLE browser so the user can sign in (incl. MFA) once.

    Blocks until login completes or times out. Returns the profile name.
    """
    import shutil
    from .auth import browser_session, ensure_logged_in, capture_app_headers
    from .client import GarminClient, GarminAPIError

    if fresh and PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
        log("Cleared saved session; starting fresh.")

    log("A browser window will open. Sign in to Garmin there (email, password, MFA).")
    with browser_session(PROFILE_DIR, headless=False) as page:
        ensure_logged_in(page)
        app_headers = capture_app_headers(page)
        try:
            name = GarminClient(page, app_headers=app_headers).display_name
            log(f"Logged in and verified (profile: {name}). You can close this and pull data.")
            return name
        except GarminAPIError as e:
            raise RuntimeError(f"Logged in, but the API check failed: {e}")


def is_logged_in() -> bool:
    """Best-effort check whether a saved session exists (profile on disk)."""
    return PROFILE_DIR.exists() and any(PROFILE_DIR.iterdir())
