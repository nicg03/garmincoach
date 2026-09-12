"""
Programmatic entry points for the desktop app (and reusable by the CLI).

Everything the GUI needs lives here so the interface layer stays thin:
  - pull(...)             fetch a date range, summarize, store, export a run file
  - fill_the_blank(...)   pull from the last stored day up to today
  - export_history(...)   write ONE combined JSON of the entire local history
  - last_pulled_date()    the most recent day we already have
  - db_counts()           how much history is stored

  - push(...)             send stored summaries to the deployed site
  - link(...)             open the site, get a sync token approved in-browser
  - sync(...)             fill_the_blank followed by push, the one-button path

Progress is reported through an optional `log` callback (a function taking a
single string). The GUI passes one that appends to its status pane; the CLI can
pass `print`. Pulls run Playwright's sync API, which is fine inside a worker
thread as long as it's the only Playwright running in that thread.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / ".browser-profile"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "garmin.db"

HISTORY_PATH = DATA_DIR / "garmin_history.json"
CONFIG_PATH = DATA_DIR / "config.json"

# Rows per request when pushing. Keeps the first full-history upload from
# becoming one enormous POST, and gives the log something to report.
PUSH_CHUNK = 250

# How far back a routine push re-sends. Devices sync late and Garmin revises
# yesterday's sleep, so overlapping a little keeps the site honest.
PUSH_DEFAULT_DAYS = 30


def _noop(_msg: str) -> None:
    pass


# ---- site configuration -----------------------------------------------------
def load_config() -> dict:
    """Where the site lives and the token to push with.

    Kept in `data/config.json`, which is gitignored along with the rest of your
    personal data, so the token never lands in the repo.
    """
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(**values) -> dict:
    """Merge non-empty values into the saved config."""
    config = load_config()
    config.update({k: v for k, v in values.items() if v})
    DATA_DIR.mkdir(exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    return config


def _site_base(url: str) -> str:
    return (url or "").rstrip("/")


def link(url: str | None = None, log=_noop, open_browser: bool = True) -> dict:
    """Open the site in a browser, wait for the owner to approve, and save
    the sync token. Other users never need to copy a token by hand."""
    import time
    import webbrowser

    config = load_config()
    site_url = _site_base(url or config.get("site_url", ""))
    if not site_url:
        raise RuntimeError(
            "Pass the site address once:\n"
            "  python -m garmin_sync link --url https://your-app.up.railway.app")

    begin = _get_json(f"{site_url}/api/pair/begin")
    device_code = begin["device_code"]
    user_code = begin["user_code"]
    interval = max(1, int(begin.get("interval") or 2))
    deadline = time.time() + int(begin.get("expires_in") or 600)
    pair_url = f"{site_url}/pair/{user_code}"

    log(f"Open this page and sign in if asked:\n  {pair_url}")
    log(f"Then click Connect (code {user_code}). Waiting…")
    if open_browser:
        try:
            webbrowser.open(pair_url)
        except Exception:
            pass

    while time.time() < deadline:
        time.sleep(interval)
        try:
            result = _post_json(f"{site_url}/api/pair/poll", None,
                                {"device_code": device_code})
        except RuntimeError as e:
            # 404 = expired/unknown; anything else is worth stopping on.
            if "404" in str(e):
                raise RuntimeError(
                    "That link expired. Run `python -m garmin_sync link` again.") from None
            raise
        if result.get("status") == "ready":
            token = result["sync_token"]
            save_config(site_url=site_url, sync_token=token)
            email = result.get("email") or "your account"
            log(f"Linked to {site_url} as {email}. Token saved in data/config.json.")
            return {"site_url": site_url, "email": email}

    raise RuntimeError("Timed out waiting for approval. Run link again.")


def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, method="GET",
                                 headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30,
                                    context=_ssl_context()) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = (e.read() or b"").decode(errors="replace")[:300]
        raise RuntimeError(f"The site answered {e.code}. {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach {url}: {e.reason}") from None


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


def _ingest_url(site_url: str) -> str:
    return site_url.rstrip("/") + "/api/ingest"


def _ssl_context():
    """macOS Python builds often ship without a CA bundle; certifi fills that."""
    import ssl
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _post_json(url: str, token: str | None, payload: dict, timeout: int = 90) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=_ssl_context()) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = (e.read() or b"").decode(errors="replace")[:300]
        hint = {401: " -- the site doesn't recognise this token; run "
                     "`python -m garmin_sync link` again",
                404: " -- check the site URL",
                503: " -- the site isn't ready to accept data yet"}.get(e.code, "")
        raise RuntimeError(f"The site answered {e.code}{hint}. {detail}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach {url}: {e.reason}") from None


def push(since: date | str | None = None, url: str | None = None,
         token: str | None = None, log=_noop) -> dict:
    """Send stored summaries to the deployed site.

    `since` accepts a date, an ISO string, or "all" for the whole history.
    The URL and token are remembered in `data/config.json` after the first
    successful call, so later pushes need no arguments.
    """
    from .store import Store

    config = load_config()
    site_url = url or config.get("site_url", "")
    sync_token = token or config.get("sync_token", "")
    if not site_url or not sync_token:
        raise RuntimeError(
            "No site configured yet. Sign up on the site, then link this "
            "computer once:\n"
            "  python -m garmin_sync link --url https://your-app.up.railway.app")
    if not DB_PATH.exists():
        raise RuntimeError("No local history yet -- pull some data first.")

    if since == "all":
        start = None
    elif since is None:
        start = (date.today() - timedelta(days=PUSH_DEFAULT_DAYS)).isoformat()
    else:
        start = since.isoformat() if isinstance(since, date) else str(since)

    store = Store(DB_PATH)
    try:
        day_rows = store.days_between(start, None)
        activity_rows = store.activities_between(start, None)
    finally:
        store.close()

    chunks = [{"days": day_rows[i:i + PUSH_CHUNK], "activities": []}
              for i in range(0, len(day_rows), PUSH_CHUNK)]
    chunks += [{"days": [], "activities": activity_rows[i:i + PUSH_CHUNK]}
               for i in range(0, len(activity_rows), PUSH_CHUNK)]
    if not chunks:
        log("Nothing to push for that range.")
        return {"activities": 0, "days": 0}

    log(f"Publishing {len(activity_rows)} activities and {len(day_rows)} day(s) "
        f"to {site_url} ...")
    result: dict = {}
    for i, chunk in enumerate(chunks, 1):
        result = _post_json(_ingest_url(site_url), sync_token, chunk)
        if len(chunks) > 1:
            log(f"  sent {i}/{len(chunks)}")

    save_config(site_url=site_url, sync_token=sync_token)
    log(f"Site updated. It now holds {result.get('activities', '?')} activities "
        f"and {result.get('days', '?')} day(s).")
    return {"activities": len(activity_rows), "days": len(day_rows),
            "site": result}


def sync(limit: int = 50, headless: bool = True, url: str | None = None,
         token: str | None = None, log=_noop) -> dict:
    """Fetch what's missing from Garmin, then publish it. The one-button path."""
    pulled = fill_the_blank(limit=limit, headless=headless, log=log)
    pushed = push(url=url, token=token, log=log)
    return {"pulled": pulled, "pushed": pushed}


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
