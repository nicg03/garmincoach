"""
What to fetch from Garmin, and how to put the answers back together.

The browser extension knows two things about Garmin: the base URL, and that
requests need the page's CSRF header. Everything else -- which endpoints
exist, which dates are missing, how to page the activity list, what the
results mean -- lives here, on the server.

That split is the point. Adding a metric means editing this file and
deploying; the extension keeps working unchanged. The alternative would put
endpoint paths inside the extension, where every change waits on a Chrome Web
Store review.

The protocol is three short round trips:

  1. the extension fetches the profile (its one hardcoded path) and posts it
     to `/api/sync/fetchplan`
  2. this module answers with a flat list of {label, path} specs
  3. the extension fetches them all and posts `{label: result}` back to
     `/api/ingest`, where `regroup` reassembles it

Labels carry the structure, because a flat map is all the extension has to
handle. `daily::2026-09-12` means "the daily summary for that date", and
`regroup` is the only thing that needs to know it.
"""
from __future__ import annotations

from datetime import date, timedelta

from garmin_sync import endpoints as ep

# One incremental sync shouldn't hammer Garmin. A longer gap is filled over
# several runs, newest first, so the dashboard is useful immediately.
MAX_DAYS_PER_SYNC = 45

# First visit: enough recent context to draw charts, then a backfill walks
# further into the past on its own.
FIRST_RUN_DAYS = 90

# Daily wellness older than this is rarely worth the Garmin rate-limit cost.
# Activities still paginate until Garmin runs out, which is the real history.
WELLNESS_HISTORY_DAYS = 365 * 5
HISTORY_FLOOR = date(2015, 1, 1)

# Garmin's activity list pages at 50; a page covers weeks for most people.
ACTIVITIES_PER_PAGE = 50

# Incremental sync asks for several newest-first pages so a busy week still
# lands, then the direct connection keeps paging while a page comes back full.
INCREMENTAL_ACTIVITY_PAGES = 3
EXTRA_ACTIVITY_PAGES = 10

# Activity list lookback is independent of wellness `last`. If days are already
# filled through today, a window of "today only" would never recover a run
# from yesterday that failed to store.
ACTIVITY_LOOKBACK_DAYS = 14

DAY_LABELS = ("daily", "sleep", "hrv", "readiness")


def display_name(profile: dict | None) -> str | None:
    """The profile id Garmin's per-user endpoints want in their path.

    Garmin returns several id-ish fields and the one the wellness endpoints
    accept is the UUID-shaped one, so that wins when it's present.
    """
    if not isinstance(profile, dict):
        return None
    inner = profile.get("userProfile") or profile.get("userProfileDto")
    if isinstance(inner, dict):
        profile = {**profile, **inner}
    candidates = [profile.get(key) for key in
                  ("displayName", "garminGUID", "profileId", "userName", "id")]
    uuidish = [c for c in candidates if isinstance(c, str) and c.count("-") >= 4]
    if uuidish:
        return uuidish[0]
    return next((str(c) for c in candidates if c), None)


def days_to_fetch(last: str | None, today: str | None = None,
                  limit: int = MAX_DAYS_PER_SYNC) -> list[str]:
    """Which dates this sync should ask for, newest first.

    `last` is the most recent day already stored. It gets re-fetched rather
    than skipped: Garmin revises last night's sleep and HRV during the
    morning, and devices that synced late backfill into it.
    """
    end = date.fromisoformat(today) if today else date.today()
    if last:
        start = date.fromisoformat(last)
        if start > end:
            start = end
    else:
        start = end - timedelta(days=limit - 1)
    if start < HISTORY_FLOOR:
        start = HISTORY_FLOOR
    span = (end - start).days + 1
    if span > limit:
        start = end - timedelta(days=limit - 1)
        span = limit
    return [(end - timedelta(days=i)).isoformat() for i in range(span)]


def days_before(before: str, limit: int, today: str | None = None) -> list[str]:
    """The next older bite of daily wellness, walking into the past.

    `before` is the oldest day already stored. We fetch the `limit` days
    immediately older than that, and stop at five years / Garmin's era.
    """
    today_d = date.fromisoformat(today) if today else date.today()
    floor = max(HISTORY_FLOOR, today_d - timedelta(days=WELLNESS_HISTORY_DAYS))
    end = date.fromisoformat(before) - timedelta(days=1)
    if end < floor:
        return []
    start = max(floor, end - timedelta(days=limit - 1))
    return [(end - timedelta(days=i)).isoformat()
            for i in range((end - start).days + 1)]


def activity_date_window(today: str | None = None, last: str | None = None
                         ) -> tuple[str, str]:
    """Garmin startDate/endDate for an incremental activity list.

    Independent of which wellness days this run fetches. `endDate` is today
    (not tomorrow) so Garmin is not asked for a future calendar date.
    First run looks back `MAX_DAYS_PER_SYNC`; later runs use
    `ACTIVITY_LOOKBACK_DAYS`.
    """
    today_d = date.fromisoformat(today) if today else date.today()
    span = MAX_DAYS_PER_SYNC if last is None else ACTIVITY_LOOKBACK_DAYS
    start = today_d - timedelta(days=span - 1)
    if start < HISTORY_FLOOR:
        start = HISTORY_FLOOR
    return start.isoformat(), today_d.isoformat()


def activity_specs(start: int, pages: int,
                   start_date: str | None = None,
                   end_date: str | None = None) -> list[dict]:
    """Paginated activity-list specs, newest first."""
    start0 = max(0, int(start or 0))
    specs = []
    for i in range(max(0, pages)):
        offset = start0 + i * ACTIVITIES_PER_PAGE
        specs.append({
            "label": f"activities::{offset}",
            "path": ep.activities_path(
                limit=ACTIVITIES_PER_PAGE, start=offset,
                start_date=start_date, end_date=end_date),
        })
    return specs


def build_plan(profile: dict | None, last: str | None = None,
               pages: int = 1, today: str | None = None,
               limit: int = MAX_DAYS_PER_SYNC,
               backfill_before: str | None = None,
               activity_start: int = 0,
               include_meta: bool = True) -> dict:
    """The full list of specs for one sync.

    `pages` asks for more of the activity list. `activity_start` is Garmin's
    list offset, so a backfill can continue past activities already stored.
    `backfill_before` walks daily metrics older than the oldest stored day.
    """
    name = display_name(profile)
    if not name:
        raise ValueError(
            "Garmin didn't return a profile id. Open Garmin Connect, make sure "
            "you're signed in, and try again.")

    if backfill_before:
        dates = days_before(backfill_before, limit, today=today)
    else:
        dates = days_to_fetch(last, today=today, limit=limit)
    specs: list[dict] = []

    start0 = max(0, int(activity_start or 0))
    # Incremental activity lists use a fixed lookback, not the wellness
    # window. History walks the global list by offset, so no dates there.
    start_date = end_date = None
    if not backfill_before:
        start_date, end_date = activity_date_window(today=today, last=last)
    specs += activity_specs(start0, pages, start_date, end_date)

    for day in dates:
        specs += [
            {"label": f"daily::{day}",
             "path": ep.DAILY_SUMMARY.format(display_name=name, date=day)},
            {"label": f"sleep::{day}",
             "path": ep.SLEEP.format(display_name=name, date=day)},
            {"label": f"hrv::{day}", "path": ep.HRV.format(date=day)},
            {"label": f"readiness::{day}",
             "path": ep.TRAINING_READINESS.format(date=day)},
        ]

    if include_meta:
        newest = dates[0] if dates else (today or date.today().isoformat())
        oldest = dates[-1] if dates else newest
        specs += [
            {"label": "hr_zones", "path": ep.HR_ZONES},
            {"label": "power_zones", "path": ep.POWER_ZONES},
            {"label": "personal_records",
             "path": ep.PERSONAL_RECORDS.format(display_name=name)},
            {"label": "race_predictions",
             "path": ep.RACE_PREDICTIONS.format(display_name=name)},
            {"label": "training_status",
             "path": ep.TRAINING_STATUS.format(date=newest)},
            {"label": "max_metrics",
             "path": ep.MAX_METRICS.format(start=oldest, end=newest)},
        ]

    return {
        "base": ep.API_BASE,
        "display_name": name,
        "days": dates,
        "specs": specs,
        "activity_start": start0,
        "activity_start_date": start_date,
        "activity_end_date": end_date,
        "complete": not dates and pages <= 0,
    }


def regroup(results: dict) -> dict:
    """Reassemble a flat `{label: result}` map into the shape `ingest` wants.

    Unknown labels are ignored rather than rejected, so a newer extension
    sending something this server doesn't recognise degrades quietly instead
    of failing the whole sync.
    """
    activities: list = []
    days: dict[str, dict] = {}
    out: dict = {}

    for label, value in (results or {}).items():
        if not isinstance(label, str):
            continue
        kind, _, suffix = label.partition("::")
        if kind == "activities":
            activities += _activity_items(value)
        elif kind in DAY_LABELS and suffix:
            days.setdefault(suffix, {"date": suffix})[kind] = value
        elif not suffix:
            out[kind] = value

    out["activities"] = activities
    out["days"] = [days[key] for key in sorted(days)]
    return out


def _activity_items(value) -> list:
    """Garmin sometimes returns a bare list, sometimes `{activityList: [...]}`."""
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict) or value.get("__error") is not None:
        return []
    for key in ("activityList", "activities"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    if value.get("activityId") or value.get("activity_id") or value.get("id"):
        return [value]
    return []
