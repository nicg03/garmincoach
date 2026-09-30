"""Human coach ↔ athlete links, notes, and authorised cross-account reads.

Every function that returns another user's data first checks an accepted
`coaching_links` row. The AI coach in `coach.py` is unrelated.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import HTTPException

from garmin_sync import metrics

from . import db, planning
from .store import VALID_ROLES


def role_of(user: dict) -> str:
    role = user.get("role") or "athlete"
    return role if role in VALID_ROLES else "athlete"


def require_role(user: dict, expected: str) -> None:
    if role_of(user) != expected:
        raise HTTPException(403, f"This action is for {expected} accounts.")


def public_coach(user: dict) -> dict:
    return {"id": user["id"], "email": user["email"]}


def lookup_coach(email: str) -> dict | None:
    with db.store() as handle:
        found = handle.user_by_email(email)
    if not found or role_of(found) != "coach":
        return None
    return public_coach(found)


def request_link(athlete: dict, coach_id: int) -> dict:
    require_role(athlete, "athlete")
    if athlete["id"] == coach_id:
        raise HTTPException(400, "You cannot coach yourself.")
    with db.store() as handle:
        coach = handle.user_by_id(coach_id)
        if not coach or role_of(coach) != "coach":
            raise HTTPException(404, "No coach with that account.")
        link = handle.upsert_coaching_request(athlete["id"], coach_id)
    return _link_view(link, athlete_email=athlete["email"],
                      coach_email=coach["email"])


def athlete_links(athlete: dict) -> dict:
    require_role(athlete, "athlete")
    with db.store() as handle:
        rows = handle.links_for_athlete(athlete["id"])
        notes = handle.notes_for_athlete(athlete["id"])
        coaches = {}
        out_links = []
        for row in rows:
            coach = handle.user_by_id(row["coach_id"])
            email = (coach or {}).get("email") or ""
            coaches[row["coach_id"]] = email
            out_links.append(_link_view(row, athlete_email=athlete["email"],
                                        coach_email=email))
        note_views = [_note_view(n, coaches.get(n["coach_id"], ""))
                      for n in notes]
    return {"links": out_links, "notes": note_views}


def inbox(coach: dict) -> dict:
    require_role(coach, "coach")
    with db.store() as handle:
        rows = handle.links_for_coach(coach["id"], "pending")
        items = []
        for row in rows:
            athlete = handle.user_by_id(row["athlete_id"])
            items.append(_link_view(
                row,
                athlete_email=(athlete or {}).get("email") or "",
                coach_email=coach["email"]))
    return {"requests": items}


def set_link_status(coach: dict, link_id: int, status: str) -> dict:
    require_role(coach, "coach")
    if status not in ("accepted", "rejected"):
        raise HTTPException(400, "Status must be accepted or rejected.")
    with db.store() as handle:
        link = handle.coaching_link_by_id(link_id)
        if not link or link["coach_id"] != coach["id"]:
            raise HTTPException(404, "Unknown request.")
        if link["status"] != "pending":
            raise HTTPException(409, "That request is no longer pending.")
        updated = handle.set_coaching_status(link_id, status)
        athlete = handle.user_by_id(updated["athlete_id"])
    return _link_view(updated, athlete_email=(athlete or {}).get("email") or "",
                      coach_email=coach["email"])


def assert_coach_of(coach: dict, athlete_id: int) -> None:
    require_role(coach, "coach")
    with db.store() as handle:
        link = handle.coaching_link(athlete_id, coach["id"])
    if not link or link["status"] != "accepted":
        raise HTTPException(403, "You are not coaching that athlete.")


def roster(coach: dict) -> dict:
    require_role(coach, "coach")
    with db.store() as handle:
        rows = handle.links_for_coach(coach["id"], "accepted")
        cards = []
        for row in rows:
            athlete = handle.user_by_id(row["athlete_id"])
            if not athlete:
                continue
            cards.append(_athlete_card(athlete))
    return {"athletes": cards}


def add_note(coach: dict, athlete_id: int, kind: str, text: str,
             session_id: str | None = None) -> dict:
    assert_coach_of(coach, athlete_id)
    body = (text or "").strip()
    if not body:
        raise HTTPException(400, "Write a comment first.")
    if len(body) > 4000:
        raise HTTPException(400, "Comment is too long.")
    with db.store() as handle:
        note = handle.add_coach_note(coach["id"], athlete_id, kind, body,
                                     session_id)
    return _note_view(note, coach["email"])


def list_notes(coach: dict, athlete_id: int) -> dict:
    assert_coach_of(coach, athlete_id)
    with db.store() as handle:
        notes = handle.notes_for_athlete(athlete_id, coach["id"])
    return {"notes": [_note_view(n, coach["email"]) for n in notes]}


def athlete_overview(coach: dict, athlete_id: int) -> dict:
    assert_coach_of(coach, athlete_id)
    with db.store() as handle:
        athlete = handle.user_by_id(athlete_id)
    if not athlete:
        raise HTTPException(404, "Unknown athlete.")
    card = _athlete_card(athlete)
    card["profile"] = planning.athlete(athlete_id)
    card["races"] = planning.list_races(athlete_id)
    card.update(list_notes(coach, athlete_id))
    return card


def _link_view(row: dict, athlete_email: str, coach_email: str) -> dict:
    return {
        "id": row["id"],
        "athlete_id": row["athlete_id"],
        "coach_id": row["coach_id"],
        "status": row["status"],
        "created": row["created"],
        "updated": row["updated"],
        "athlete_email": athlete_email,
        "coach_email": coach_email,
    }


def _note_view(row: dict, coach_email: str) -> dict:
    return {
        "id": row["id"],
        "coach_id": row["coach_id"],
        "athlete_id": row["athlete_id"],
        "kind": row["kind"],
        "text": row["text"],
        "session_id": row["session_id"],
        "created": row["created"],
        "coach_email": coach_email,
    }


def _athlete_card(athlete: dict) -> dict:
    uid = athlete["id"]
    info = db.status(uid)
    end = date.today().isoformat()
    start = (date.fromisoformat(end) - timedelta(days=89)).isoformat()
    first = info.get("first")
    if first and first > start:
        start = first
    day_rows, activity_rows = db.window(uid, start, end)
    payload = metrics.build(day_rows, activity_rows, start=start, end=end,
                            visible_from=start)
    headline = payload.get("headline") or {}
    races = planning.list_races(uid)
    today = date.today().isoformat()
    upcoming = [r for r in races if (r.get("date") or "") >= today]
    upcoming.sort(key=lambda r: r.get("date") or "")
    last_act = activity_rows[-1] if activity_rows else None
    profile = planning.athlete(uid)
    return {
        "id": uid,
        "email": athlete["email"],
        "last": info.get("last"),
        "days": info.get("days") or 0,
        "activities": info.get("activities") or 0,
        "ctl": headline.get("ctl"),
        "form": headline.get("form"),
        "atl": headline.get("atl"),
        "week_km": headline.get("week_km"),
        "hrv": headline.get("hrv"),
        "acwr": headline.get("acwr"),
        "hrv_delta": headline.get("hrv_delta"),
        "resting_hr_delta": headline.get("resting_hr_delta"),
        "sleep_score": headline.get("sleep_score"),
        "next_race": upcoming[0] if upcoming else None,
        "goals": profile.get("notes") or "",
        "last_activity": {
            "start": last_act.get("start"),
            "type": last_act.get("type"),
            "name": last_act.get("name"),
        } if last_act else None,
    }
