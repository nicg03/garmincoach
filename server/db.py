"""
Database access for the server: one short-lived connection per request,
always scoped to a user.

Every function here takes a `user_id` and passes it straight through to the
store, which keeps it in the primary key. There is no query in this file that
can return another account's rows.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date

from . import config
from .store import EmailTaken, Store, normalise_email, open_store  # noqa: F401

SESSION_SECRET_KEY = "session_secret"


@contextmanager
def store():
    with open_store(config.DB_PATH) as handle:
        yield handle


def session_secret() -> str:
    """The key session cookies are signed with.

    An explicit `SESSION_SECRET` wins. Otherwise one is minted on first boot
    and kept in the database, so sign-ins survive restarts and a fresh deploy
    needs no configuration at all.
    """
    if config.SESSION_SECRET:
        return config.SESSION_SECRET
    from .store import new_token

    with store() as handle:
        return handle.ensure_setting(SESSION_SECRET_KEY, new_token)


def status(user_id: int) -> dict:
    with store() as handle:
        meta = handle.meta(user_id)
        last_ingest = (meta.get("last_ingest") or {}).get("value")
        return {
            "last": handle.last_day(user_id),
            "first": handle.first_day(user_id),
            "last_ingest": last_ingest if isinstance(last_ingest, dict) else None,
            **handle.counts(user_id),
        }


def window(user_id: int, start: str | None,
           end: str | None) -> tuple[list[dict], list[dict]]:
    """Day and activity summaries for one user over a date range."""
    with store() as handle:
        return (handle.days_between(user_id, start, end),
                handle.activities_between(user_id, start, end))


def ingest(user_id: int, activities: list[dict], days: list[dict],
           meta: dict | None = None) -> dict:
    """Upsert a pushed bundle. Idempotent: re-pushing the same range only
    refreshes rows whose summaries changed, which is what late device syncs
    and Garmin's own overnight revisions produce.
    """
    stored = {"activities": 0, "days": 0, "meta": 0, "skipped": 0}
    with store() as handle:
        for activity in activities:
            if not isinstance(activity, dict) or activity.get("id") is None:
                stored["skipped"] += 1
                continue
            handle.upsert_activity(user_id, activity)
            stored["activities"] += 1
        for day in days:
            if not isinstance(day, dict) or not day.get("date"):
                stored["skipped"] += 1
                continue
            handle.upsert_day(user_id, day)
            stored["days"] += 1
        for key, value in (meta or {}).items():
            if key == "last_ingest":
                continue
            handle.set_meta(user_id, key, value)
            stored["meta"] += 1
        handle.commit()
    try:
        from . import planning
        stored["matched"] = planning.match_completed(user_id)
    except Exception:
        stored["matched"] = 0
    return stored


def meta(user_id: int) -> dict:
    """Zones, personal records and predictions as last pulled."""
    with store() as handle:
        return handle.meta(user_id)


def get_briefing(user_id: int, day: str) -> str | None:
    with store() as handle:
        return handle.briefing(user_id, day)


def save_briefing(user_id: int, day: str, text: str, created: str) -> None:
    with store() as handle:
        handle.save_briefing(user_id, day, text, created)


def coach_calls_today(user_id: int) -> int:
    with store() as handle:
        return handle.coach_calls(user_id, date.today().isoformat())


def record_coach_call(user_id: int) -> int:
    with store() as handle:
        return handle.record_coach_call(user_id, date.today().isoformat())
