"""
Command line interface.

    python -m garmin_sync login              # one-time manual login
    python -m garmin_sync pull --since 14d   # fetch everything + summarize + export

There is no capture/discovery step: the endpoint paths are baked in (they're
stable; only Garmin's auth changed in 2026). `pull` fetches all data types
for the whole range concurrently in a single pass.

Playwright is imported lazily so `--help` works without it installed.
"""
from __future__ import annotations
import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE_DIR = ROOT / ".browser-profile"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "garmin.db"


def _parse_since(s: str) -> date:
    m = re.fullmatch(r"(\d+)d", s.strip())
    if m:
        return date.today() - timedelta(days=int(m.group(1)))
    return date.fromisoformat(s)


def cmd_login(args):
    import shutil
    from .auth import browser_session, ensure_logged_in
    from .client import GarminClient, GarminAPIError

    if args.fresh and PROFILE_DIR.exists():
        shutil.rmtree(PROFILE_DIR)
        print("Cleared saved session; starting fresh.")

    with browser_session(PROFILE_DIR, headless=False) as page:
        ensure_logged_in(page)
        from .auth import capture_app_headers
        app_headers = capture_app_headers(page)
        try:
            name = GarminClient(page, app_headers=app_headers).display_name
            csrf = "csrf ok" if app_headers.get("connect-csrf-token") else "no csrf captured"
            print(f"Logged in and API access verified ({csrf}, profile: {name}).")
        except GarminAPIError as e:
            print(f"Logged in, but the API check failed: {e}\n"
                  "Try `python -m garmin_sync login --fresh`.")


def cmd_pull(args):
    from .auth import browser_session, ensure_logged_in, capture_app_headers
    from .client import GarminClient, GarminAPIError
    from . import summarize as sm
    from .store import Store

    DATA_DIR.mkdir(exist_ok=True)
    since_date = _parse_since(args.since)
    days = [(date.today() - timedelta(days=i)).isoformat()
            for i in range((date.today() - since_date).days + 1)]

    store = Store(DB_PATH)
    with browser_session(PROFILE_DIR, headless=args.headless) as page:
        ensure_logged_in(page)
        app_headers = capture_app_headers(page)
        if "connect-csrf-token" not in app_headers:
            print("  warning: couldn't capture the CSRF token; calls may 403.", file=sys.stderr)
        gc = GarminClient(page, app_headers=app_headers)

        try:
            _ = gc.display_name
        except GarminAPIError as e:
            print(f"\nCouldn't reach the Garmin API: {e}", file=sys.stderr)
            print("Try `python -m garmin_sync login` again -- the session may have expired.", file=sys.stderr)
            store.close()
            return

        # Build every request up front, then fire them all concurrently.
        specs = [gc.activities_spec(limit=args.limit)]
        for d in days:
            specs.extend(gc.day_specs(d))

        print(f"  fetching {len(specs)} endpoints...", file=sys.stderr)
        results = gc.batch(specs)

        def ok(v):
            return v if isinstance(v, (list, dict)) and not (isinstance(v, dict) and "__error" in v) else None

        # Activities
        acts = ok(results.get("activities")) or []
        kept = 0
        for a in acts:
            start = (a.get("startTimeLocal") or "")[:10]
            if start and start < since_date.isoformat():
                continue
            store.upsert_activity(sm.summarize_activity(a, full=args.full))
            kept += 1

        # Days
        for d in days:
            day = sm.build_day(
                d,
                daily=ok(results.get(f"daily::{d}")),
                sleep=ok(results.get(f"sleep::{d}")),
                hrv=ok(results.get(f"hrv::{d}")),
                readiness=ok(results.get(f"readiness::{d}")),
                full=args.full,
            )
            store.upsert_day(day)

        # Report anything that failed so it's visible, not silent.
        errs = sorted({str(v["__error"]) for v in results.values()
                       if isinstance(v, dict) and "__error" in v})
        print(f"  activities: {kept} | days: {len(days)}"
              + (f" | some endpoints returned: {', '.join(errs)}" if errs else ""), file=sys.stderr)

    store.commit()
    out_path = DATA_DIR / f"garmin_{date.today().isoformat()}.json"
    counts = store.export(out_path, since=since_date.isoformat())
    store.close()
    print(f"\nWrote {out_path}")
    print(f"  {counts['activities']} activities, {counts['days']} days")
    print("Drag that file into Claude and ask away.")


def cmd_doctor(args):
    import time
    from .auth import browser_session, ensure_logged_in, get_live_token, get_access_token
    from .client import GarminClient, GarminAPIError

    calls = {}
    sample = {}
    with browser_session(PROFILE_DIR, headless=False) as page:
        ensure_logged_in(page)

        def on_resp(resp):
            u = resp.url
            if "-service/" not in u:
                return
            parts = u.split("/")
            host = parts[2]
            path = "/" + "/".join(parts[3:]).split("?")[0]
            try:
                has_auth = bool(resp.request.all_headers().get("authorization"))
            except Exception:
                has_auth = None
            calls.setdefault((host, path), {"auth": has_auth, "status": resp.status})
            if (not sample and "/gc-api/" in u and resp.status == 200
                    and "json" in (resp.headers or {}).get("content-type", "")):
                try:
                    sample.update(resp.request.all_headers())
                except Exception:
                    pass

        page.on("response", on_resp)
        page.goto("https://connect.garmin.com/modern/", wait_until="domcontentloaded")
        time.sleep(8)
        try:
            page.goto("https://connect.garmin.com/modern/activities", wait_until="domcontentloaded")
            time.sleep(5)
        except Exception:
            pass
        try:
            page.remove_listener("response", on_resp)
        except Exception:
            pass

        print("\n=== API calls the Garmin app made ===")
        if not calls:
            print("  (none captured -- the app may not have loaded past Cloudflare)")
        for (host, path), info in sorted(calls.items()):
            print(f"  {info['status']}  auth={info['auth']}  {host}{path}")

        live = get_live_token(page)
        ls = get_access_token(page)
        print(f"\nlive-sniffed token: {'yes' if live else 'NONE'} | localStorage token: {'yes' if ls else 'NONE'}")
        print("(tokens are no longer used -- auth is via session cookies)")

        print("\n=== headers of a working gc-api request (cookie value hidden) ===")
        if not sample:
            print("  (none captured)")
        for k, v in sorted(sample.items()):
            if k.lower() == "cookie":
                v = f"<{len(v)} chars present>"
            print(f"  {k}: {v}")

        from .auth import capture_app_headers
        app_headers = capture_app_headers(page)
        try:
            name = GarminClient(page, app_headers=app_headers).display_name
            print(f"\nprofile via cookies + csrf + /gc-api: OK ({name})")
        except GarminAPIError as e:
            print(f"\nprofile via cookies + csrf + /gc-api: FAIL {str(e)[:160]}")

    print("\n--- paste everything above this line back ---")


def build_parser():
    p = argparse.ArgumentParser(prog="garmin_sync", description="Pull your Garmin data via a real browser session.")
    sub = p.add_subparsers(dest="cmd", required=True)
    lg = sub.add_parser("login", help="one-time manual login in a visible browser")
    lg.add_argument("--fresh", action="store_true", help="wipe the saved session and log in clean")
    lg.set_defaults(func=cmd_login)
    pl = sub.add_parser("pull", help="fetch everything, summarize, export")
    pl.add_argument("--since", default="14d", help="e.g. 14d or 2026-06-01")
    pl.add_argument("--limit", type=int, default=50, help="max recent activities to scan")
    pl.add_argument("--full", action="store_true", help="keep raw data (no summarizing)")
    pl.add_argument("--headless", action="store_true", help="hide the browser (may fail token capture)")
    pl.set_defaults(func=cmd_pull)

    sub.add_parser("doctor", help="diagnose login/token/API issues").set_defaults(func=cmd_doctor)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
