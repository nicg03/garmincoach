"""
Exercise the extension's side of the protocol against a running server.

The extension is hard to test in a browser without a real Garmin session, but
everything it relies on from the site is plain HTTP, and that part is worth
having a check for: the fetch plan, the raw ingest, and the metrics that come
out the other end.

Payloads here are shaped like Garmin's real answers, including a failed
endpoint, because dropping those markers is exactly the behaviour that keeps
one dead metric from costing a whole sync.

    python3 -m uvicorn server.main:app --port 8077
    python3 scripts/check_extension_path.py
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import date

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8077"

PROFILE = {
    "displayName": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    "profileId": 987654,
    "userName": "tester",
}


def call(path: str, payload=None, token: str | None = None, method: str | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method or ("POST" if data else "GET"),
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = (e.read() or b"").decode(errors="replace")
        raise SystemExit(f"{path} answered {e.code}: {body[:400]}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"Couldn't reach {BASE}: {e.reason}. Start the server first.") from None


def garmin_answers(days: list[str]) -> dict:
    """A flat {label: result} map like the content script hands back."""
    results: dict = {}
    activities = []
    for index, day in enumerate(days[:3]):
        activities.append({
            "activityId": 1000 + index,
            "activityName": "Morning Run",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": f"{day} 07:12:00",
            "duration": 3600.0,
            "distance": 12000.0,
            "averageHR": 145,
            "maxHR": 168,
            "averageSpeed": 3.33,
            "elevationGain": 120,
            "calories": 800,
            "aerobicTrainingEffect": 3.4,
            "anaerobicTrainingEffect": 0.6,
            "activityTrainingLoad": 180,
        })
    results["activities::0"] = activities

    for position, day in enumerate(days):
        results[f"daily::{day}"] = {
            "totalSteps": 11000 + position,
            "restingHeartRate": 48,
            "minHeartRate": 42,
            "maxHeartRate": 171,
            "averageStressLevel": 28,
            "maxStressLevel": 82,
            "bodyBatteryHighestValue": 91,
            "bodyBatteryLowestValue": 22,
            "activeKilocalories": 940,
            "moderateIntensityMinutes": 30,
            "vigorousIntensityMinutes": 20,
        }
        results[f"sleep::{day}"] = {
            "dailySleepDTO": {
                "sleepTimeSeconds": 27000,
                "deepSleepSeconds": 5400,
                "lightSleepSeconds": 16200,
                "remSleepSeconds": 4500,
                "awakeSleepSeconds": 900,
                "sleepScores": {"overall": {"value": 82}},
                "averageRespirationValue": 14.2,
                "restingHeartRate": 48,
            }
        }
        results[f"hrv::{day}"] = {
            "hrvSummary": {"lastNightAvg": 62 + position, "status": "BALANCED"}
        }
        # Readiness is the one that routinely 403s on watches that don't
        # compute it, so half the days carry the failure marker instead.
        results[f"readiness::{day}"] = (
            [{"score": 74}] if position % 2 == 0 else {"__error": 403}
        )

    results["hr_zones"] = [{"sport": "RUNNING", "zone1Floor": 101, "zone5Floor": 172}]
    results["power_zones"] = {"__error": 403}
    results["race_predictions"] = {"time5K": 1320, "time10K": 2760}
    return results


def main() -> None:
    stamp = date.today().isoformat().replace("-", "")
    email = f"check+{stamp}@example.com"
    password = "check-password-123"

    print(f"→ signing up {email}")
    try:
        account = call("/api/signup", {"email": email, "password": password})
    except SystemExit:
        account = call("/api/login", {"email": email, "password": password})
    token = account["user"]["sync_token"]
    print(f"  sync token: {token[:12]}…")

    print("→ GET /api/sync/state")
    state = call("/api/sync/state", token=token)
    print(f"  last={state['last']} days={state['days']}")

    print("→ POST /api/sync/fetchplan")
    plan = call("/api/sync/fetchplan", {"profile": PROFILE}, token=token)
    specs = plan["specs"]
    print(f"  {len(specs)} specs over {len(plan['days'])} days, base {plan['base']}")
    assert plan["display_name"] == PROFILE["displayName"], "wrong profile id chosen"
    assert all(s["path"].startswith("/") for s in specs), "a path wasn't root-relative"
    assert any("racepredictions" in s["path"] for s in specs), "extras missing"
    sample = next(s for s in specs if s["label"].startswith("daily::"))
    assert PROFILE["displayName"] in sample["path"], "profile id not substituted"

    print("→ POST /api/ingest with raw Garmin answers")
    days = plan["days"]
    stored = call("/api/ingest", {"source": "extension",
                                  "results": garmin_answers(days)}, token=token)
    print(f"  stored {stored['stored']}")
    assert stored["stored"]["activities"] == 3, stored
    assert stored["stored"]["days"] == len(days), stored
    assert stored["stored"]["meta"] == 2, "failed extras should be dropped"

    print("→ GET /api/metrics (cookie auth)")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor())
    login = urllib.request.Request(
        BASE + "/api/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    opener.open(login, timeout=30).read()
    with opener.open(BASE + "/api/metrics?days=30", timeout=30) as response:
        metrics = json.loads(response.read())
    head = metrics["headline"]
    print(f"  ctl={head['ctl']} atl={head['atl']} hrv={head['hrv']} "
          f"sleep={head['sleep_score']} readiness={head['readiness']}")
    assert head["ctl"], "no training load came through"
    assert head["hrv"], "no HRV came through"
    assert head["sleep_score"] == 82, head

    with opener.open(BASE + "/api/meta", timeout=30) as response:
        meta = json.loads(response.read())
    print(f"  meta keys: {sorted(meta)}")
    assert "hr_zones" in meta and "power_zones" not in meta, meta

    print("\nall good: the extension's contract with the site holds.")


if __name__ == "__main__":
    main()
