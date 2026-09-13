"""Unit checks for the extension contract, raw ingest, and the export parser."""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

from server import garmin_export, garmin_fetch, ingest
from garmin_sync import summarize as sm


PROFILE = {
    "displayName": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
    "profileId": 987654,
    "userName": "tester",
}


def test_fetchplan_substitutes_profile_and_stays_root_relative():
    plan = garmin_fetch.build_plan(PROFILE, last=None, today="2026-09-12",
                                   pages=2, limit=5)
    assert plan["display_name"] == PROFILE["displayName"]
    assert plan["base"].startswith("https://connect.garmin.com")
    assert all(spec["path"].startswith("/") and ".." not in spec["path"]
               for spec in plan["specs"])
    daily = next(s for s in plan["specs"] if s["label"].startswith("daily::"))
    assert PROFILE["displayName"] in daily["path"]
    assert any("racepredictions" in s["path"] for s in plan["specs"])
    assert any(s["label"] == "activities::50" for s in plan["specs"])


def test_regroup_and_normalise_drop_failed_endpoints():
    day = "2026-09-10"
    results = {
        "activities::0": [{
            "activityId": 42,
            "activityName": "Morning Run",
            "activityType": {"typeKey": "running"},
            "startTimeLocal": f"{day} 07:12:00",
            "duration": 3600.0,
            "distance": 12000.0,
            "averageHR": 145,
            "activityTrainingLoad": 180,
            "aerobicTrainingEffect": 3.2,
        }],
        f"daily::{day}": {"totalSteps": 11000, "restingHeartRate": 48},
        f"sleep::{day}": {
            "dailySleepDTO": {
                "sleepTimeSeconds": 27000,
                "deepSleepSeconds": 5400,
                "lightSleepSeconds": 16200,
                "remSleepSeconds": 4500,
                "awakeSleepSeconds": 900,
                "sleepScores": {"overall": {"value": 82}},
            }
        },
        f"hrv::{day}": {"hrvSummary": {"lastNightAvg": 64, "status": "BALANCED"}},
        f"readiness::{day}": {"__error": 403},
        "hr_zones": [{"sport": "RUNNING", "zone1Floor": 101}],
        "power_zones": {"__error": 403},
        "race_predictions": {"time5K": 1320},
    }
    raw = garmin_fetch.regroup(results)
    activities, days, meta = ingest.normalise({"raw": raw})
    assert len(activities) == 1
    assert activities[0]["id"] == 42
    assert activities[0]["duration_s"] == 3600
    assert days[0]["date"] == day
    assert days[0]["sleep"]["score"] == 82
    assert days[0]["hrv_avg"] == 64
    assert "training_readiness" not in days[0]
    assert "hr_zones" in meta
    assert "power_zones" not in meta
    assert "race_predictions" in meta


def test_summarize_keeps_zones_and_splits():
    summary = sm.summarize_activity({
        "activityId": 7,
        "activityName": "Intervals",
        "activityType": {"typeKey": "running"},
        "startTimeLocal": "2026-09-11 18:00:00",
        "duration": 2400,
        "distance": 8000,
        "hrTimeInZone": [{"zoneNumber": 1, "secs": 600},
                         {"zoneNumber": 5, "secs": 180}],
        "splits": [{"distance": 1000, "duration": 240, "averageHR": 150}],
        "vO2MaxValue": 54,
    })
    assert summary["vo2max"] == 54
    assert summary["hr_zones"][0]["secs"] == 600
    assert summary["splits"][0]["distance_m"] == 1000


def _export_zip() -> bytes:
    activities = [{
        "summarizedActivitiesExport": [{
            "activityId": 99,
            "name": "Long run",
            "activityType": "running",
            "startTimeLocal": "2026-08-01 08:00:00",
            "duration": 5_400_000,  # ms
            "distance": 1_800_000,  # cm → 18 km
            "avgHr": 142,
            "maxHr": 168,
            "calories": 1100,
            "elevationGain": 25000,  # cm → 250 m
            "activityTrainingLoad": 220,
            "aerobicTrainingEffect": 3.8,
        }]
    }]
    sleep = [{
        "calendarDate": "2026-08-01",
        "sleepTimeSeconds": 28800,
        "deepSleepSeconds": 5400,
        "lightSleepSeconds": 18000,
        "remSleepSeconds": 5400,
        "awakeSleepSeconds": 600,
        "sleepScores": {"overall": {"value": 88}},
        "restingHeartRate": 46,
        "avgOvernightHrv": 71,
        "hrvStatus": "BALANCED",
    }]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("DI_CONNECT/DI-Connect-Fitness/summarizedActivities.json",
                    json.dumps(activities))
        zf.writestr("DI_CONNECT/DI-Connect-Wellness/2026_sleepData.json",
                    json.dumps(sleep))
        zf.writestr("DI_CONNECT/DI-Connect-Uploaded-Files/note.txt", "skip me")
    return buf.getvalue()


def test_garmin_export_reads_activities_and_sleep(tmp_path: Path):
    archive = tmp_path / "export.zip"
    archive.write_bytes(_export_zip())
    found = garmin_export.read_archive(archive)
    assert found.report["activities"] == 1
    assert found.report["days"] == 1
    act = found.activities[0]
    assert act["id"] == 99
    assert act["duration_s"] == 5400
    assert act["distance_m"] == 18000
    assert act["elevation_gain_m"] == 250
    day = found.days[0]
    assert day["date"] == "2026-08-01"
    assert day["sleep"]["score"] == 88
    assert day["sleep"]["total_h"] == 8
    assert day["hrv_avg"] == 71


def test_local_push_still_accepted_as_summaries():
    activities, days, meta = ingest.normalise({
        "activities": [{"id": 1, "start": "2026-09-01 07:00:00",
                        "type": "running", "duration_s": 1800,
                        "distance_m": 5000}],
        "days": [{"date": "2026-09-01", "daily": {"steps": 8000}}],
    })
    assert activities[0]["id"] == 1
    assert days[0]["date"] == "2026-09-01"
    assert meta == {}


def test_days_to_fetch_refetches_last_day():
    days = garmin_fetch.days_to_fetch("2026-09-10", today="2026-09-12")
    assert days[0] == "2026-09-12"
    assert "2026-09-10" in days
    assert len(days) == 3


def test_days_before_walks_into_the_past():
    days = garmin_fetch.days_before("2026-08-14", 5, today="2026-09-12")
    assert days[0] == "2026-08-13"
    assert days[-1] == "2026-08-09"
    assert garmin_fetch.days_before("2015-01-01", 45, today="2026-09-12") == []


def test_backfill_plan_has_older_days_and_can_skip_meta():
    plan = garmin_fetch.build_plan(
        PROFILE, today="2026-09-12", backfill_before="2026-08-14",
        pages=2, limit=4, activity_start=50, include_meta=False)
    assert plan["days"][0] == "2026-08-13"
    assert any(s["label"] == "activities::50" for s in plan["specs"])
    assert any(s["label"] == "activities::100" for s in plan["specs"])
    assert not any(s["label"] == "hr_zones" for s in plan["specs"])
    empty = garmin_fetch.build_plan(
        PROFILE, today="2026-09-12", backfill_before="2015-01-01",
        pages=0, include_meta=False)
    assert empty["complete"]
    assert empty["specs"] == []


def test_upsert_day_merges_later_chunks(tmp_path):
    from server.store import open_store

    with open_store(tmp_path / "t.db") as handle:
        user = handle.create_user("a@b.c", "hash")
        handle.upsert_day(user["id"], {"date": "2026-09-01", "steps": 8000})
        handle.upsert_day(user["id"], {"date": "2026-09-01", "sleep_score": 82})
        handle.commit()
        row = handle.days_between(user["id"], "2026-09-01", "2026-09-01")[0]
        assert row["steps"] == 8000
        assert row["sleep_score"] == 82
