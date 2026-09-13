"""
Summarization -- the "get everything but be wise on memory" layer.

Garmin returns a lot of second-by-second and minute-by-minute data. Feeding
raw streams to an LLM is wasteful and mostly noise for training analysis. So:

  - Activities: keep the summary fields (type, duration, distance, HR, pace,
    training effect, load, elevation). Drop GPS tracks and per-second samples
    unless full=True.
  - Daily wellness: collapse per-minute arrays into a compact roll-up
    (min / avg / max, plus a few coarse hourly buckets). Sleep keeps stage
    totals and scores, not the per-epoch hypnogram.

Everything is defensive: Garmin's payloads vary by device and firmware, so
missing keys just become None rather than crashing.
"""
from __future__ import annotations
from statistics import mean


def _g(d, *keys, default=None):
    """Nested safe-get: _g(obj, 'a', 'b') -> obj['a']['b'] or default."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _round(x, n=2):
    return round(x, n) if isinstance(x, (int, float)) else x


def downsample(series, buckets=8):
    """Average a numeric series into `buckets` coarse points (e.g. hourly-ish)."""
    nums = [v for v in (series or []) if isinstance(v, (int, float))]
    if not nums:
        return []
    if len(nums) <= buckets:
        return [_round(v, 1) for v in nums]
    size = len(nums) / buckets
    out = []
    for i in range(buckets):
        chunk = nums[round(i * size):round((i + 1) * size)] or [nums[-1]]
        out.append(_round(mean(chunk), 1))
    return out


def _stat_block(values):
    nums = [v for v in (values or []) if isinstance(v, (int, float))]
    if not nums:
        return None
    return {"min": _round(min(nums), 1), "avg": _round(mean(nums), 1),
            "max": _round(max(nums), 1), "trend": downsample(nums)}


def summarize_activity(a: dict, full: bool = False) -> dict:
    if full:
        return a
    return {
        "id": a.get("activityId"),
        "name": a.get("activityName"),
        "type": _g(a, "activityType", "typeKey"),
        "start": a.get("startTimeLocal"),
        "duration_s": _round(a.get("duration")),
        "distance_m": _round(a.get("distance")),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "avg_speed": _round(a.get("averageSpeed")),
        "elevation_gain_m": _round(a.get("elevationGain")),
        "calories": a.get("calories"),
        "aerobic_te": a.get("aerobicTrainingEffect"),
        "anaerobic_te": a.get("anaerobicTrainingEffect"),
        "training_load": a.get("activityTrainingLoad"),
        "avg_power": a.get("avgPower") or a.get("normPower"),
        "norm_power": a.get("normPower"),
        "avg_cadence": a.get("averageRunningCadenceInStepsPerMinute")
        or a.get("averageBikingCadenceInRevPerMinute")
        or a.get("averageSwimCadenceInStrokesPerMinute"),
        "vo2max": a.get("vO2MaxValue"),
        "avg_stride_cm": _round(a.get("avgStrideLength")),
        "max_speed": _round(a.get("maxSpeed")),
        "elevation_loss_m": _round(a.get("elevationLoss")),
        "min_hr": a.get("minHR"),
        "hr_zones": _hr_zones(a),
        "splits": _compact_splits(a),
    }


def _hr_zones(a: dict) -> list[dict] | None:
    """Keep time-in-zone if Garmin attached it to the activity list item."""
    raw = a.get("hrTimeInZone") or a.get("heartRateZones") or a.get("hrZones")
    if not isinstance(raw, list) or not raw:
        return None
    out = []
    for i, z in enumerate(raw, 1):
        if isinstance(z, dict):
            secs = z.get("secs") or z.get("timeInZone") or z.get("duration")
            out.append({"zone": z.get("zoneNumber") or i, "secs": secs})
        elif isinstance(z, (int, float)):
            out.append({"zone": i, "secs": z})
    return out or None


def _compact_splits(a: dict) -> list[dict] | None:
    splits = a.get("splits") or _g(a, "splitSummaries") or []
    if not isinstance(splits, list) or not splits:
        return None
    out = []
    for s in splits[:80]:
        if not isinstance(s, dict):
            continue
        out.append({
            "distance_m": _round(s.get("distance") or s.get("distanceInMeters")),
            "duration_s": _round(s.get("duration") or s.get("elapsedDuration")),
            "avg_hr": s.get("averageHR") or s.get("avgHR"),
            "elevation_gain_m": _round(s.get("elevationGain")),
        })
    return out or None


def summarize_sleep(s: dict) -> dict | None:
    dto = _g(s, "dailySleepDTO") or s
    if not dto:
        return None
    return {
        "score": _g(dto, "sleepScores", "overall", "value"),
        "total_h": _round((dto.get("sleepTimeSeconds") or 0) / 3600, 2),
        "deep_h": _round((dto.get("deepSleepSeconds") or 0) / 3600, 2),
        "light_h": _round((dto.get("lightSleepSeconds") or 0) / 3600, 2),
        "rem_h": _round((dto.get("remSleepSeconds") or 0) / 3600, 2),
        "awake_h": _round((dto.get("awakeSleepSeconds") or 0) / 3600, 2),
        "avg_resp": dto.get("averageRespirationValue"),
        "avg_spo2": dto.get("averageSpO2Value"),
        "resting_hr": dto.get("restingHeartRate"),
    }


def summarize_daily(summary: dict) -> dict:
    return {
        "steps": summary.get("totalSteps"),
        "resting_hr": summary.get("restingHeartRate"),
        "min_hr": summary.get("minHeartRate"),
        "max_hr": summary.get("maxHeartRate"),
        "avg_stress": summary.get("averageStressLevel"),
        "max_stress": summary.get("maxStressLevel"),
        "body_battery_high": summary.get("bodyBatteryHighestValue"),
        "body_battery_low": summary.get("bodyBatteryLowestValue"),
        "active_kcal": summary.get("activeKilocalories"),
        "intensity_min": (summary.get("moderateIntensityMinutes") or 0)
        + (summary.get("vigorousIntensityMinutes") or 0),
    }


def build_day(date, daily=None, sleep=None, hrv=None, readiness=None, full=False):
    """Assemble one compact day record from the various wellness endpoints."""
    rec = {"date": date}
    if daily:
        rec["daily"] = daily if full else summarize_daily(daily)
    if sleep:
        rec["sleep"] = sleep if full else summarize_sleep(sleep)
    if hrv:
        rec["hrv_avg"] = _g(hrv, "hrvSummary", "lastNightAvg")
        rec["hrv_status"] = _g(hrv, "hrvSummary", "status")
    if readiness:
        r = readiness[0] if isinstance(readiness, list) and readiness else readiness
        rec["training_readiness"] = _g(r, "score")
    return rec
