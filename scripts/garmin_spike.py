"""
Can this machine talk to Garmin without a browser?

Answers the three questions the direct connection depends on, using the same
fetch plan the extension runs and the same summarizer the site stores with:

  1. does the login (including a second MFA step) work from here
  2. do saved tokens reload in a fresh client and refresh themselves
  3. do the gc-api paths in `garmin_sync/endpoints.py` answer on
     connectapi.garmin.com with a bearer token

Run it on your own computer first, then from the deployed container
(`railway ssh`, then `python scripts/garmin_spike.py`) -- Cloudflare treats
datacentre addresses differently from home ones, and that is the part a local
run cannot tell you.

    pip install garminconnect curl_cffi
    python scripts/garmin_spike.py --email you@example.com
    python scripts/garmin_spike.py --tokens data/spike_tokens.json   # reuse
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from garmin_sync import endpoints as ep  # noqa: E402
from server import garmin_fetch, ingest  # noqa: E402


def egress_ip() -> str:
    try:
        import requests
        return requests.get("https://api.ipify.org", timeout=5).text.strip()
    except Exception as e:  # noqa: BLE001
        return f"unknown ({type(e).__name__})"


def login(email: str) -> str:
    from garminconnect import Garmin

    password = getpass.getpass("Garmin password: ")
    api = Garmin(email, password, return_on_mfa=True)
    started = time.time()
    status, _ = api.login()
    if status == "needs_mfa":
        code = input("Garmin sent a code. MFA code: ").strip()
        api.resume_login(None, code)
    print(f"login ok in {time.time() - started:.1f}s")
    return api.client.dumps()


def fetch(tokens: str, days: int) -> None:
    from garminconnect import Garmin

    api = Garmin()
    api.client.loads(tokens)
    before = api.client.di_token

    profile = api.client.connectapi(ep.PROFILE)
    print(f"profile ok: {garmin_fetch.display_name(profile)}")

    plan = garmin_fetch.build_plan(profile, last=None, pages=1, limit=days)
    results: dict = {}
    failed: dict = {}
    started = time.time()
    for spec in plan["specs"]:
        try:
            results[spec["label"]] = api.client.connectapi(spec["path"])
        except Exception as e:  # noqa: BLE001
            results[spec["label"]] = {"__error": str(e)[:80]}
            failed[spec["label"]] = str(e)[:120]
    elapsed = time.time() - started

    activities, day_rows, meta = ingest.normalise(
        {"raw": garmin_fetch.regroup(results)})
    print(f"{len(plan['specs'])} requests in {elapsed:.1f}s, "
          f"{len(failed)} failed")
    for label, error in failed.items():
        print(f"  {label}: {error}")
    print(f"summarized: {len(activities)} activities, {len(day_rows)} days, "
          f"meta keys {sorted(meta)}")
    if api.client.di_token != before:
        print("the access token was refreshed during the run")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--email", help="Garmin account email (fresh login)")
    parser.add_argument("--tokens", default=str(ROOT / "data" / "spike_tokens.json"),
                        help="where tokens are written and read")
    parser.add_argument("--days", type=int, default=3,
                        help="days of wellness to fetch")
    args = parser.parse_args()

    print(f"egress ip: {egress_ip()}")
    path = Path(args.tokens)
    if args.email:
        tokens = login(args.email)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tokens)
        path.chmod(0o600)
        print(f"tokens written to {path}")
    elif path.exists():
        tokens = path.read_text()
    else:
        parser.error("no saved tokens yet: pass --email for the first run")

    fetch(tokens, args.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
