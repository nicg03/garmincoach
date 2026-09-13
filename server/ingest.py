"""
Turning what a client sent into rows we can store.

Two kinds of client push data here, and the difference is deliberate:

  - The **local Python sync** summarizes on the user's own machine and sends
    finished records. It has always worked that way and still does.
  - The **browser extension** sends Garmin's raw JSON and lets this module
    summarize it. That's the important one: every change to `summarize.py`
    then reaches every user on the next deploy, instead of waiting for a new
    extension version to clear the Chrome Web Store review queue.

So the extension stays a dumb pipe and the thinking stays here. The same
[garmin_sync.summarize] module runs in both places, so a day summarized on a
Mac and a day summarized here are the same shape.

Everything is defensive. A sync fetches dozens of endpoints and some of them
fail routinely (Garmin 403s a metric your watch doesn't record), so failures
arrive as `{"__error": 403}` markers and are simply dropped.
"""
from __future__ import annotations

from typing import Any

from garmin_sync import summarize as sm

# Per-sync extras that aren't per-day: zones, records, predictions. Stored as
# one blob each so adding a new one needs no migration.
META_KEYS = (
    "hr_zones",
    "power_zones",
    "personal_records",
    "race_predictions",
    "training_status",
    "max_metrics",
)


def failed(value: Any) -> bool:
    """Whether a fetch result is an error marker rather than data."""
    return not isinstance(value, (list, dict)) or (
        isinstance(value, dict) and "__error" in value)


def usable(value: Any) -> Any:
    """The value if the fetch succeeded, else None."""
    return None if failed(value) else value


def _activities_from(raw: Any) -> list[dict]:
    """Summaries for every activity in a raw activity-list response.

    Garmin's list endpoint is paginated, so a client may send either one page
    or several concatenated; both arrive here as a flat list.
    """
    if failed(raw):
        return []
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        summary = sm.summarize_activity(item)
        if summary.get("id") is not None:
            out.append(summary)
    return out


def _day_from(entry: Any) -> dict | None:
    """One compact day record from a bundle of that day's raw endpoints."""
    if not isinstance(entry, dict):
        return None
    day = entry.get("date")
    if not day:
        return None
    record = sm.build_day(
        day,
        daily=usable(entry.get("daily")),
        sleep=usable(entry.get("sleep")),
        hrv=usable(entry.get("hrv")),
        readiness=usable(entry.get("readiness")),
    )
    # A date and nothing else would overwrite a good day with an empty one if
    # the client happened to sync while Garmin had no data yet.
    return record if len(record) > 1 else None


def normalise(payload: dict) -> tuple[list[dict], list[dict], dict]:
    """Split a pushed payload into (activities, days, meta) ready to store.

    Accepts both shapes. `raw` is summarized here; `activities`/`days` are
    taken as already-summarized records. A payload may carry both, and the
    raw side wins on conflict because it's the fresher of the two.
    """
    activities: list[dict] = []
    days: list[dict] = []
    meta: dict[str, Any] = {}

    ready_activities = payload.get("activities")
    if isinstance(ready_activities, list):
        activities += [a for a in ready_activities if isinstance(a, dict)]
    ready_days = payload.get("days")
    if isinstance(ready_days, list):
        days += [d for d in ready_days if isinstance(d, dict)]

    raw = payload.get("raw")
    if isinstance(raw, dict):
        activities += _activities_from(raw.get("activities"))
        for entry in raw.get("days") or []:
            record = _day_from(entry)
            if record:
                days.append(record)
        for key in META_KEYS:
            value = usable(raw.get(key))
            if value:
                meta[key] = value

    return _dedupe(activities, "id"), _dedupe(days, "date"), meta


def _dedupe(rows: list[dict], key: str) -> list[dict]:
    """Last write wins, order preserved -- the raw side is appended after the
    pre-summarized side, so it's the one that survives."""
    seen: dict[Any, int] = {}
    out: list[dict] = []
    for row in rows:
        identity = row.get(key)
        if identity is None:
            continue
        if identity in seen:
            out[seen[identity]] = row
        else:
            seen[identity] = len(out)
            out.append(row)
    return out
