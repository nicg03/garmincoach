"""
Reading the zip Garmin emails you when you ask for your data.

This is the no-installation path: request the export from your Garmin account
(Account → Data Management → Export Your Data), drop the zip on the site, and
years of history arrive at once. The extension handles every day after that,
but it can only page back so far politely, so this is how you get the past.

The format is undocumented and drifts between exports, so nothing here trusts
a path or a key. Files are classified by name fragment, every field is read
through a list of plausible spellings, and anything unrecognised is counted
and reported rather than silently dropped. The endpoint hands that report
back to the user, which is the only honest way to do this: if a future export
renames something, you see "0 days recognised" instead of a quietly empty
dashboard.

Units are the real trap. The export stores what the device stored -- integers
scaled by a thousand or a hundred -- so `duration` is milliseconds and
`distance` is centimetres, unlike the web API's seconds and metres. Rather
than trusting that, each value is converted and then sanity-checked against a
plausible range, and implausible ones are left out.

Activity FIT files under DI-Connect-Uploaded-Files are deliberately ignored.
`summarizedActivities.json` already carries every field the dashboard uses,
and parsing FIT would mean a new dependency for no gain.
"""
from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Exports of a decade of history reach a few hundred megabytes.
MAX_UPLOAD_BYTES = 600 * 1024 * 1024

# A single member that expands beyond this is not something we need; the big
# ones are the nested archives of FIT files, which we skip anyway.
MAX_MEMBER_BYTES = 80 * 1024 * 1024

# Total expanded bytes we're willing to read, as zip-bomb insurance.
MAX_TOTAL_BYTES = 1200 * 1024 * 1024


class NotAnExport(Exception):
    """The upload wasn't a Garmin export we could make sense of."""


@dataclass
class Found:
    activities: list[dict] = field(default_factory=list)
    days: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)


# ---- small helpers ----------------------------------------------------------
def _first(source: dict, *names, default=None):
    """The first of several possible spellings that's actually present."""
    for name in names:
        if isinstance(source, dict) and source.get(name) is not None:
            return source[name]
    return default


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _scaled(value, divisor: float, ceiling: float) -> float | None:
    """Convert a device-scaled integer, dropping implausible results.

    The ceiling is what makes this safe: if a future export changes a unit,
    the value lands outside a physically sensible range and is discarded
    instead of drawing a 4000 km run on the chart.
    """
    raw = _num(value)
    if raw is None or raw < 0:
        return None
    out = raw / divisor
    return round(out, 2) if out <= ceiling else None


def _date_of(value) -> str | None:
    """An ISO date from the several ways the export writes one."""
    if isinstance(value, str) and len(value) >= 10:
        candidate = value[:10]
        if candidate[4] == "-" and candidate[7] == "-":
            return candidate
    if isinstance(value, dict):
        return _date_of(_first(value, "date", "calendarDate"))
    if isinstance(value, (int, float)) and value > 0:
        from datetime import datetime, timezone
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _rows(payload: Any) -> list[dict]:
    """Every dict-shaped record in a payload, whatever it's wrapped in.

    Export files are variously a list of records, a list holding one wrapper
    object whose key contains the records, or a bare object. This flattens all
    three, and the visit budget keeps a pathologically nested file from
    spinning.
    """
    out: list[dict] = []
    stack: list[Any] = list(payload) if isinstance(payload, list) else [payload]
    budget = 500_000
    while stack and budget > 0:
        budget -= 1
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, dict):
            if _looks_like_record(item):
                out.append(item)
                continue
            nested = [v for v in item.values() if isinstance(v, (list, dict))]
            if nested:
                stack.extend(nested)
            else:
                out.append(item)
    return out


def _looks_like_record(item: dict) -> bool:
    """Whether a dict is a data record rather than a wrapper around them."""
    markers = ("calendarDate", "activityId", "sleepStartTimestampGMT",
               "beginTimestamp", "uuid", "startTimeLocal")
    return any(marker in item for marker in markers)


# ---- activities -------------------------------------------------------------
def _activity_type(value) -> str | None:
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, dict):
        key = _first(value, "typeKey", "key", "name")
        return key.lower() if isinstance(key, str) else None
    return None


def activity_from_export(row: dict) -> dict | None:
    """One activity summary, shaped exactly like `summarize_activity` makes.

    Same keys, so these rows are indistinguishable downstream from ones the
    extension or the local sync produced.
    """
    activity_id = _first(row, "activityId", "activity_id", "id")
    if not isinstance(activity_id, (int, float, str)):
        return None
    try:
        activity_id = int(activity_id)
    except (TypeError, ValueError):
        return None

    start = _first(row, "startTimeLocal", "beginTimestamp", "startTimeGmt")
    day = _date_of(start)
    if not day:
        return None
    # `days_between` on the site compares on the first ten characters of
    # `start`, so a date alone is enough, but keeping a time makes the
    # activity list read sensibly.
    when = start if isinstance(start, str) and len(start) > 10 else f"{day} 00:00:00"

    return {
        "id": activity_id,
        "name": _first(row, "name", "activityName"),
        "type": _activity_type(_first(row, "activityType", "sportType")),
        "start": when,
        # Milliseconds in the export, seconds in the web API. A single
        # activity beyond 24 hours is not something we need to support.
        "duration_s": _scaled(_first(row, "duration", "elapsedDuration"),
                              1000, 86400),
        "distance_m": _scaled(_first(row, "distance"), 100, 500_000),
        "avg_hr": _num(_first(row, "avgHr", "averageHR")),
        "max_hr": _num(_first(row, "maxHr", "maxHR")),
        "avg_speed": _scaled(_first(row, "avgSpeed", "averageSpeed"), 100, 40),
        "elevation_gain_m": _scaled(_first(row, "elevationGain"), 100, 30_000),
        "calories": _num(_first(row, "calories")),
        "aerobic_te": _num(_first(row, "aerobicTrainingEffect")),
        "anaerobic_te": _num(_first(row, "anaerobicTrainingEffect")),
        "training_load": _num(_first(row, "activityTrainingLoad",
                                     "trainingLoad")),
        "avg_power": _num(_first(row, "avgPower", "averagePower")),
        "avg_cadence": _scaled(_first(row, "avgRunCadence", "averageRunningCadenceInStepsPerMinute",
                                      "avgBikeCadence"), 1, 300),
    }


# ---- wellness ---------------------------------------------------------------
def sleep_from_export(row: dict) -> tuple[str, dict] | None:
    """A night's sleep in the shape `summarize_sleep` produces."""
    day = _date_of(_first(row, "calendarDate", "sleepEndTimestampGMT"))
    if not day:
        return None
    total = _num(_first(row, "sleepTimeSeconds"))
    stages = {
        "deep_h": _scaled(_first(row, "deepSleepSeconds"), 3600, 24),
        "light_h": _scaled(_first(row, "lightSleepSeconds"), 3600, 24),
        "rem_h": _scaled(_first(row, "remSleepSeconds"), 3600, 24),
        "awake_h": _scaled(_first(row, "awakeSleepSeconds"), 3600, 24),
    }
    if total is None:
        # Older exports omit the total but carry the stages it's made of.
        parts = [v for k, v in stages.items() if k != "awake_h" and v is not None]
        total = sum(parts) * 3600 if parts else None
    if total is None:
        return None

    record = {
        "score": _sleep_score(row),
        "total_h": _scaled(total, 3600, 24),
        **stages,
        "avg_resp": _num(_first(row, "averageRespirationValue", "avgRespirationValue")),
        "avg_spo2": _num(_first(row, "averageSpO2Value", "avgSpo2Value")),
        "resting_hr": _num(_first(row, "restingHeartRate")),
    }
    return day, record


def _sleep_score(row: dict) -> float | None:
    """The overall sleep score, flat in some exports and nested in others."""
    scores = row.get("sleepScores")
    if isinstance(scores, dict):
        overall = scores.get("overall")
        if isinstance(overall, dict):
            value = _num(overall.get("value"))
            if value is not None:
                return value
        value = _num(overall)
        if value is not None:
            return value
    return _num(_first(row, "sleepScore", "overallSleepScore"))


def daily_from_export(row: dict) -> tuple[str, dict] | None:
    """A day's wellness roll-up in the shape `summarize_daily` produces."""
    day = _date_of(_first(row, "calendarDate", "calendarDateLocal"))
    if not day:
        return None
    moderate = _num(_first(row, "moderateIntensityMinutes",
                           "moderateIntensityDurationInSeconds")) or 0
    vigorous = _num(_first(row, "vigorousIntensityMinutes",
                           "vigorousIntensityDurationInSeconds")) or 0
    # Some exports store these as seconds rather than minutes.
    if moderate > 1440 or vigorous > 1440:
        moderate, vigorous = moderate / 60, vigorous / 60

    record = {
        "steps": _num(_first(row, "totalSteps", "steps")),
        "resting_hr": _num(_first(row, "restingHeartRate", "minAvgHeartRate")),
        "min_hr": _num(_first(row, "minHeartRate")),
        "max_hr": _num(_first(row, "maxHeartRate")),
        "avg_stress": _num(_first(row, "averageStressLevel", "avgStressLevel")),
        "max_stress": _num(_first(row, "maxStressLevel")),
        "body_battery_high": _num(_first(row, "bodyBatteryHighestValue",
                                         "maxBodyBattery")),
        "body_battery_low": _num(_first(row, "bodyBatteryLowestValue",
                                        "minBodyBattery")),
        "active_kcal": _num(_first(row, "activeKilocalories",
                                   "activeCalories")),
        "intensity_min": round(moderate + vigorous) or None,
    }
    if all(v is None for v in record.values()):
        return None
    return day, record


def hrv_from_export(row: dict) -> tuple[str, dict] | None:
    """Overnight HRV, which recent exports fold into the sleep records."""
    day = _date_of(_first(row, "calendarDate"))
    if not day:
        return None
    avg = _num(_first(row, "avgOvernightHrv", "lastNightAvg", "weeklyAvg"))
    status = _first(row, "hrvStatus", "status")
    if avg is None and not status:
        return None
    out = {}
    if avg is not None:
        out["hrv_avg"] = avg
    if isinstance(status, str):
        out["hrv_status"] = status
    return day, out


# ---- the archive ------------------------------------------------------------
def _classify(name: str) -> str | None:
    """Which parser a member belongs to, by name fragment."""
    lowered = name.lower()
    if lowered.endswith(".zip") or not lowered.endswith(".json"):
        return None
    if "summarizedactivities" in lowered:
        return "activities"
    if "sleepdata" in lowered:
        return "sleep"
    if "udsfile" in lowered or "aggregator" in lowered:
        return "daily"
    if "hrvdata" in lowered or "hrv_" in lowered:
        return "hrv"
    return None


def read_archive(path: Path) -> Found:
    """Everything we can recognise in an export, ready to store."""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise NotAnExport(
            "That isn't a zip file. Upload the archive Garmin emailed you, "
            "without unpacking it first.") from None

    with archive:
        members = [m for m in archive.infolist() if not m.is_dir()]
        if not members:
            raise NotAnExport("That zip is empty.")

        classified = [(m, _classify(m.filename)) for m in members]
        interesting = [(m, kind) for m, kind in classified if kind]
        if not interesting:
            raise NotAnExport(
                "No Garmin data files in there. The archive should contain a "
                "DI_CONNECT folder -- if you unpacked and repacked it, upload "
                "the original instead.")

        activities: dict[int, dict] = {}
        days: dict[str, dict] = {}
        counts = {"activities": 0, "sleep": 0, "daily": 0, "hrv": 0}
        skipped = {"too_big": 0, "unreadable": 0, "unrecognised_rows": 0}
        total = 0

        for member, kind in interesting:
            if member.file_size > MAX_MEMBER_BYTES:
                skipped["too_big"] += 1
                continue
            total += member.file_size
            if total > MAX_TOTAL_BYTES:
                break
            try:
                payload = json.loads(archive.read(member).decode("utf-8-sig"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                skipped["unreadable"] += 1
                continue

            for row in _rows(payload):
                if kind == "activities":
                    summary = activity_from_export(row)
                    if summary:
                        activities[summary["id"]] = summary
                        counts["activities"] += 1
                    else:
                        skipped["unrecognised_rows"] += 1
                    continue

                parsed = {"sleep": sleep_from_export,
                          "daily": daily_from_export,
                          "hrv": hrv_from_export}[kind](row)
                if not parsed:
                    skipped["unrecognised_rows"] += 1
                    continue
                day, record = parsed
                target = days.setdefault(day, {"date": day})
                if kind == "hrv":
                    target.update(record)
                else:
                    target[kind] = record
                counts[kind] += 1

                # Recent exports carry overnight HRV inside the sleep record,
                # so the chart gets it without a separate file.
                if kind == "sleep":
                    folded = hrv_from_export(row)
                    if folded:
                        target.update(folded[1])

    day_rows = [days[key] for key in sorted(days)]
    report = {
        "files_read": len(interesting),
        "records": counts,
        "skipped": skipped,
        "activities": len(activities),
        "days": len(day_rows),
        "from": day_rows[0]["date"] if day_rows else None,
        "to": day_rows[-1]["date"] if day_rows else None,
    }
    return Found(activities=list(activities.values()), days=day_rows,
                 report=report)
