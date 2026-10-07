"""
How long the heavy pages take for one athlete with a long history.

Builds a throwaway database with one account holding `--years` of daily
wellness and ~5 runs a week, shaped like Garmin's real answers and stored
through the same ingest path a sync uses. Then it times the endpoints a
dashboard visit hits, cold (first call of the day) and warm (cached), and
turns that into a rough page-loads-per-second figure for one process.

    .venv/bin/python scripts/bench.py --years 5 --runs 5
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ENDPOINTS = [
    "/api/status",
    "/api/metrics?days=90",
    "/api/metrics?days=365",
    "/api/performance",
    "/api/plan",
    "/api/day/decide",
    "/api/workouts",
    "/api/insights",
]

# What opening the Today page and then Training costs, roughly.
PAGE_VISIT = ["/api/status", "/api/metrics?days=90", "/api/day/decide",
              "/api/plan", "/api/performance"]


def synthetic_history(years: int, seed: int = 7) -> dict:
    rng = random.Random(seed)
    today = date.today()
    days, activities = [], []
    for i in range(years * 365):
        day = today - timedelta(days=i)
        iso = day.isoformat()
        days.append({
            "date": iso,
            "daily": {"totalSteps": rng.randint(4000, 16000),
                      "restingHeartRate": rng.randint(44, 56),
                      "averageStressLevel": rng.randint(18, 40),
                      "bodyBatteryHighestValue": rng.randint(60, 100),
                      "bodyBatteryLowestValue": rng.randint(5, 40),
                      "activeKilocalories": rng.randint(300, 1200)},
            "sleep": {"dailySleepDTO": {
                "sleepTimeSeconds": rng.randint(5, 9) * 3600,
                "deepSleepSeconds": rng.randint(3000, 7000),
                "lightSleepSeconds": rng.randint(9000, 16000),
                "remSleepSeconds": rng.randint(3000, 7000),
                "awakeSleepSeconds": rng.randint(300, 2400),
                "sleepScores": {"overall": {"value": rng.randint(55, 92)}}}},
            "hrv": {"hrvSummary": {"lastNightAvg": rng.randint(45, 80),
                                   "weeklyAvg": rng.randint(50, 70)}},
        })
        if day.weekday() in (0, 1, 3, 5, 6):
            km = rng.uniform(6, 22) if day.weekday() == 6 else rng.uniform(5, 12)
            pace = rng.uniform(4.4, 5.8) * 60
            start = datetime(day.year, day.month, day.day, 7, rng.randint(0, 59))
            activities.append({
                "activityId": 10_000_000 + i,
                "activityName": "Run",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": start.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": km * pace, "distance": km * 1000,
                "averageHR": rng.randint(135, 165), "maxHR": rng.randint(165, 185),
                "averageSpeed": 1000 / pace,
                "activityTrainingLoad": rng.uniform(30, 220),
                "aerobicTrainingEffect": rng.uniform(1.5, 4.5),
            })
    return {"raw": {"days": days, "activities": activities}}


def timed(client, path: str) -> float:
    started = time.perf_counter()
    response = client.get(path)
    elapsed = (time.perf_counter() - started) * 1000
    if response.status_code != 200:
        raise SystemExit(f"{path} answered {response.status_code}: {response.text[:200]}")
    return elapsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--dir", default=None, help="where the throwaway database goes")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(dir=args.dir) as tmp:
        from server import config

        config.DB_PATH = Path(tmp) / "site.db"
        config.GARMIN_SCHEDULER = False
        from fastapi.testclient import TestClient

        from server import db, ingest
        from server.main import app

        client = TestClient(app)
        response = client.post("/api/signup", json={
            "email": "bench@example.com", "password": "password1"})
        user_id = response.json()["user"]["id"]

        started = time.perf_counter()
        activities, days, meta = ingest.normalise(synthetic_history(args.years))
        stored = db.ingest(user_id, activities, days, meta)
        load_s = time.perf_counter() - started
        size_mb = config.DB_PATH.stat().st_size / 1e6
        wal = config.DB_PATH.with_name("site.db-wal")
        if wal.exists():
            size_mb += wal.stat().st_size / 1e6
        print(f"history: {stored['days']} days, {stored['activities']} activities "
              f"stored in {load_s:.1f}s, database {size_mb:.1f} MB")

        print(f"\n{'endpoint':28} {'cold ms':>9} {'warm ms':>9}")
        warm_by_path = {}
        for path in ENDPOINTS:
            db.changed(user_id)
            cold = timed(client, path)
            warm = statistics.median(timed(client, path) for _ in range(args.runs))
            warm_by_path[path] = warm
            print(f"{path:28} {cold:9.1f} {warm:9.1f}")

        db.changed(user_id)
        cold_visit = sum(timed(client, p) for p in PAGE_VISIT)
        warm_visit = sum(warm_by_path[p] for p in PAGE_VISIT)
        print(f"\npage visit ({len(PAGE_VISIT)} calls): cold {cold_visit:.0f} ms, "
              f"warm {warm_visit:.0f} ms")
        print(f"one process, CPU-bound: ~{1000 / warm_visit:.1f} warm visits/s, "
              f"~{1000 / cold_visit:.1f} cold visits/s")


if __name__ == "__main__":
    main()
