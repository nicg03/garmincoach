"""
Planning and daily decisions on top of the deterministic engine.

This module is the only place that writes plan_sessions. The extension
never invents a workout: it pulls ready-made Garmin operations from the
outbox and reports the ids back.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from uuid import uuid4

from garmin_sync import garmin_workout, library, metrics, performance, planner
from garmin_sync.workout_dsl import WorkoutError, validate

from . import coach, config, db


def _today() -> str:
    return date.today().isoformat()


def athlete(user_id: int) -> dict:
    with db.store() as handle:
        return handle.get_athlete(user_id)


def save_athlete(user_id: int, profile: dict) -> dict:
    current = athlete(user_id)
    allowed = {k: profile.get(k) for k in (
        "sex", "birth_year", "weight_kg", "hr_max", "hr_rest", "hr_threshold",
        "ftp", "vdot", "units", "availability", "extras", "notes")}
    merged = {**current, **{k: v for k, v in allowed.items() if v is not None}}
    with db.store() as handle:
        return handle.set_athlete(user_id, merged)


def list_races(user_id: int) -> list[dict]:
    with db.store() as handle:
        return handle.list_races(user_id)


def save_race(user_id: int, payload: dict) -> dict:
    race_id = payload.get("id") or str(uuid4())
    day = payload.get("date")
    if not day:
        raise ValueError("A race needs a date.")
    date.fromisoformat(day[:10])
    race = {
        "id": race_id,
        "date": day[:10],
        "name": (payload.get("name") or "Race").strip()[:80],
        "distance_m": payload.get("distance_m") or payload.get("distance"),
        "priority": payload.get("priority") or "A",
        "goal_time": payload.get("goal_time"),
        "elevation_m": payload.get("elevation_m"),
        "notes": payload.get("notes") or "",
        "sport": payload.get("sport") or "running",
    }
    with db.store() as handle:
        return handle.upsert_race(user_id, race)


def delete_race(user_id: int, race_id: str) -> None:
    with db.store() as handle:
        handle.delete_race(user_id, race_id)


def generate_plan(user_id: int, race_id: str, extras: list[str] | None = None,
                  notes: str | None = None) -> dict:
    with db.store() as handle:
        race = handle.race(user_id, race_id)
        profile = handle.get_athlete(user_id)
        races = handle.list_races(user_id)
    if not race:
        raise ValueError("Unknown race.")
    days, activities = db.window(user_id, None, None)
    built = planner.generate(race, profile, activities, days, extras=extras,
                             races=races)
    plan = {
        "id": str(uuid4()),
        "race_id": race_id,
        "status": "draft",
        "created": _today(),
        "notes": notes or "",
        **built,
    }
    if notes and config.coach_enabled():
        extra = _llm_rationale(user_id, plan, notes)
        if extra:
            plan["rationale"] = (plan.get("rationale") or "") + "\n\n" + extra
    with db.store() as handle:
        handle.save_plan(user_id, plan)
    return handle_get_plan(user_id, plan["id"])


def handle_get_plan(user_id: int, plan_id: str | None = None) -> dict | None:
    with db.store() as handle:
        if plan_id:
            return handle.get_plan(user_id, plan_id)
        return handle.active_plan(user_id) or (
            handle.list_plans(user_id)[0] if handle.list_plans(user_id) else None)


def list_plans(user_id: int) -> list[dict]:
    with db.store() as handle:
        return handle.list_plans(user_id)


def patch_plan(user_id: int, plan_id: str, op: dict) -> dict:
    with db.store() as handle:
        plan = handle.get_plan(user_id, plan_id)
    if not plan:
        raise ValueError("Unknown plan.")
    updated = planner.apply_patch(plan, op)
    updated["id"] = plan["id"]
    updated["status"] = plan.get("status") or "draft"
    updated["created"] = plan.get("created")
    updated["race_id"] = plan.get("race_id")
    with db.store() as handle:
        handle.save_plan(user_id, updated)
    return handle_get_plan(user_id, plan_id)


def update_session(user_id: int, session_id: str, changes: dict) -> dict:
    with db.store() as handle:
        session = handle.session(user_id, session_id)
        if not session:
            raise ValueError("Unknown session.")
        if changes.get("workout"):
            session["workout"] = validate(changes["workout"])
            session["est_load"] = session["workout"].get("est_load")
            session["kind"] = session["workout"].get("kind") or session.get("kind")
            session["sport"] = session["workout"].get("sport") or session.get("sport")
        if changes.get("date"):
            session["date"] = changes["date"][:10]
        if changes.get("state"):
            session["state"] = changes["state"]
        session["revision"] = int(session.get("revision") or 1) + 1
        plan_id = session.get("plan_id")
        # Recover plan_id from the plans table if the payload didn't keep it.
        if not plan_id:
            for plan in handle.list_plans(user_id):
                if any(s["id"] == session_id for s in
                       handle.sessions_for_plan(user_id, plan["id"])):
                    plan_id = plan["id"]
                    break
        if not plan_id:
            raise ValueError("Session is not attached to a plan.")
        session["plan_id"] = plan_id
        handle.upsert_session(user_id, plan_id, session)
        handle.commit()
        return session


def activate_plan(user_id: int, plan_id: str) -> dict:
    """Mark future sessions queued so the next extension sync writes them."""
    with db.store() as handle:
        plan = handle.get_plan(user_id, plan_id)
        if not plan:
            raise ValueError("Unknown plan.")
        for other in handle.list_plans(user_id):
            if other["id"] != plan_id and other.get("status") == "active":
                other["status"] = "archived"
                handle.save_plan(user_id, other)
        today = _today()
        horizon = (date.today() + timedelta(days=14)).isoformat()
        for session in plan.get("sessions") or []:
            if (session.get("date") or "") < today:
                continue
            if (session.get("date") or "") > horizon:
                continue
            if (session.get("kind") or "") == "rest":
                continue
            if (session.get("kind") or "") == "race":
                continue
            session["state"] = "queued"
            session["plan_id"] = plan_id
            handle.upsert_session(user_id, plan_id, session)
        plan["status"] = "active"
        handle.save_plan(user_id, plan)
    return handle_get_plan(user_id, plan_id)


def outbox_ops(user_id: int) -> list[dict]:
    """Garmin operations for queued sessions. The extension is a dumb pipe."""
    with db.store() as handle:
        queued = handle.outbox(user_id)
    ops = []
    for session in queued:
        workout = session.get("workout") or {}
        if (workout.get("kind") or session.get("kind")) == "rest":
            continue
        try:
            validate(workout)
        except WorkoutError:
            continue
        sid = session["id"]
        if not session.get("garmin_workout_id"):
            create = garmin_workout.create_op(workout)
            create["session_id"] = sid
            create["ref"] = "workout"
            ops.append(create)
        else:
            update = garmin_workout.update_op(session["garmin_workout_id"], workout)
            update["session_id"] = sid
            update["ref"] = "workout"
            ops.append(update)
        if session.get("date") and not session.get("garmin_schedule_id"):
            # The workout id is filled in after create; the extension runs
            # schedule as a second step once it has that id.
            sched = {
                "op": "schedule",
                "method": "POST",
                "path": None,  # filled after create
                "body": {"date": session["date"]},
                "session_id": sid,
                "ref": "schedule",
                "depends_on": "workout",
            }
            if session.get("garmin_workout_id"):
                sched["path"] = garmin_workout.schedule_op(
                    session["garmin_workout_id"], session["date"])["path"]
            ops.append(sched)
    return ops


def ack_ops(user_id: int, results: list[dict]) -> dict:
    """Apply Garmin ids (or errors) reported by the extension."""
    updated = 0
    errors = 0
    with db.store() as handle:
        for item in results:
            if not isinstance(item, dict):
                continue
            session = handle.session(user_id, item.get("session_id") or "")
            if not session:
                continue
            if item.get("error"):
                session["last_error"] = str(item["error"])[:300]
                errors += 1
            if item.get("garmin_workout_id"):
                session["garmin_workout_id"] = int(item["garmin_workout_id"])
            if item.get("garmin_schedule_id"):
                session["garmin_schedule_id"] = int(item["garmin_schedule_id"])
            if session.get("garmin_workout_id") and session.get("garmin_schedule_id"):
                session["state"] = "pushed"
                session.pop("last_error", None)
            plan_id = session.get("plan_id")
            if plan_id:
                handle.upsert_session(user_id, plan_id, session)
                updated += 1
        handle.commit()
    return {"updated": updated, "errors": errors}


def queue_upcoming(user_id: int) -> int:
    """Keep a two-week write window full without dumping the whole plan."""
    plan = handle_get_plan(user_id)
    if not plan or plan.get("status") != "active":
        return 0
    today = _today()
    horizon = (date.today() + timedelta(days=14)).isoformat()
    n = 0
    with db.store() as handle:
        for session in plan.get("sessions") or []:
            if session.get("state") not in (None, "planned"):
                continue
            if not (today <= (session.get("date") or "") <= horizon):
                continue
            if (session.get("kind") or "") in ("rest", "race"):
                continue
            session["state"] = "queued"
            handle.upsert_session(user_id, plan["id"], session)
            n += 1
        handle.commit()
    return n


def match_completed(user_id: int) -> int:
    """Attach today's (and recent) activities to planned sessions."""
    days, activities = db.window(
        user_id, (date.today() - timedelta(days=21)).isoformat(), None)
    del days
    matched = 0
    with db.store() as handle:
        plan = handle.active_plan(user_id)
        if not plan:
            return 0
        for session in plan.get("sessions") or []:
            if session.get("state") in ("skipped", "completed"):
                continue
            activity = planner.match_activity(session, activities)
            if not activity:
                continue
            session["activity_id"] = activity.get("id")
            session["state"] = "completed"
            session["completed_load"] = activity.get("training_load")
            handle.upsert_session(user_id, plan["id"], session)
            matched += 1
        handle.commit()
    queue_upcoming(user_id)
    return matched


def adherence(user_id: int) -> dict:
    plan = handle_get_plan(user_id)
    if not plan:
        return {"planned": 0, "completed": 0, "skipped": 0, "ratio": None}
    sessions = [s for s in plan.get("sessions") or []
                if (s.get("kind") or "") not in ("rest", "race")
                and (s.get("date") or "") <= _today()]
    done = sum(1 for s in sessions if s.get("state") == "completed")
    skipped = sum(1 for s in sessions if s.get("state") == "skipped")
    return {
        "planned": len(sessions),
        "completed": done,
        "skipped": skipped,
        "ratio": round(done / len(sessions), 2) if sessions else None,
    }


def decide_today(user_id: int) -> dict:
    """keep / ease / swap / rest for today's planned session."""
    cached = None
    with db.store() as handle:
        cached = handle.get_decision(user_id, _today())
    if cached and cached.get("session_id"):
        return cached

    plan = handle_get_plan(user_id)
    today = _today()
    session = None
    if plan:
        session = next((s for s in plan.get("sessions") or []
                        if s.get("date") == today), None)
    days, activities = db.window(user_id, None, None)
    built = metrics.build(days, activities)
    head = built.get("headline") or {}
    hrv_delta = head.get("hrv_delta")
    rhr_delta = head.get("resting_hr_delta")
    sleep = head.get("sleep_score")
    readiness = head.get("readiness")
    form = head.get("form")
    acwr = head.get("acwr")

    action = "keep"
    reason = "Recovery looks in line with your baseline."
    flags = 0
    if hrv_delta is not None and hrv_delta <= -5:
        flags += 2
    if rhr_delta is not None and rhr_delta >= 3:
        flags += 1
    if sleep is not None and sleep < 60:
        flags += 1
    if readiness is not None and readiness < 40:
        flags += 2
    if acwr is not None and acwr >= 1.4:
        flags += 1
    if form is not None and form < -30:
        flags += 1

    kind = (session or {}).get("kind") or ""
    if flags >= 4 or (session and kind in ("interval", "tempo", "long") and flags >= 3):
        action = "rest"
        reason = "Several recovery markers are off. Take the day."
    elif flags >= 2 and kind in ("interval", "tempo", "long"):
        action = "ease"
        reason = "Quality today would pile on. Swap in an easy run."
    elif flags >= 2:
        action = "ease"
        reason = "Keep moving, but keep it easy."

    decision = {
        "date": today,
        "action": action,
        "reason": reason,
        "session_id": (session or {}).get("id"),
        "session": session,
        "headline": {k: head.get(k) for k in
                     ("hrv", "hrv_delta", "resting_hr", "resting_hr_delta",
                      "sleep_score", "readiness", "form", "acwr")},
    }
    with db.store() as handle:
        handle.save_decision(user_id, today, decision)
    return decision


def apply_decision(user_id: int, action: str | None = None) -> dict:
    decision = decide_today(user_id)
    chosen = action or decision.get("action") or "keep"
    session_id = decision.get("session_id")
    plan = handle_get_plan(user_id)
    if not session_id or chosen == "keep" or not plan:
        decision["applied"] = chosen
        with db.store() as handle:
            handle.save_decision(user_id, _today(), decision)
        return decision

    if chosen == "swap":
        today = _today()
        sessions = plan.get("sessions") or []
        current = next((s for s in sessions if s.get("id") == session_id), None)
        other = next(
            (s for s in sessions
             if (s.get("date") or "") > today
             and (s.get("kind") or "") in ("easy", "recovery", "rest")
             and s.get("id") != session_id),
            None)
        if current and other:
            patch_plan(user_id, plan["id"],
                       {"op": "swap", "a": current["id"], "b": other["id"]})
        else:
            patch_plan(user_id, plan["id"], {"op": "ease", "id": session_id})
            chosen = "ease"
    else:
        op = {"id": session_id}
        if chosen == "ease":
            op["op"] = "ease"
        elif chosen in ("rest", "skip"):
            op["op"] = "rest"
        else:
            decision["applied"] = chosen
            return decision
        patch_plan(user_id, plan["id"], op)

    with db.store() as handle:
        session = handle.session(user_id, session_id)
        if session and plan.get("status") == "active":
            session["state"] = "queued"
            session["garmin_schedule_id"] = None
            handle.upsert_session(user_id, plan["id"], session)
            handle.commit()
    decision["applied"] = chosen
    with db.store() as handle:
        handle.save_decision(user_id, _today(), decision)
    return decision


def race_review(user_id: int, race_id: str) -> dict:
    with db.store() as handle:
        race = handle.race(user_id, race_id)
    if not race:
        raise ValueError("Unknown race.")
    _, activities = db.window(user_id, race["date"], race["date"])
    all_acts = db.window(user_id, None, None)[1]
    perf = performance.build(all_acts, db.meta(user_id))
    actual = next((a for a in activities if planner.match_activity(
        {"date": race["date"], "sport": race.get("sport") or "running"}, [a])), None)
    pred = next((p for p in perf.get("predictions") or []
                 if abs((p.get("distance_m") or 0) - (race.get("distance_m") or 0)) < 800),
                None)
    goal_s = performance.parse_hms(race.get("goal_time"))
    actual_s = (actual or {}).get("duration_s")
    delta = (actual_s - goal_s) if actual_s and goal_s else None
    return {
        "race": race,
        "activity": actual,
        "predicted": pred,
        "vdot": perf.get("vdot"),
        "feasibility": performance.goal_feasibility(
            race.get("distance_m"), race.get("goal_time"),
            perf.get("vdot"), perf.get("predictions")),
        "splits": performance.race_splits(race.get("distance_m"), goal_s),
        "result": {
            "goal": performance.format_hms(goal_s),
            "actual": performance.format_hms(actual_s),
            "delta_s": round(delta) if delta is not None else None,
            "vs_goal": ("ahead" if delta is not None and delta < 0
                        else ("behind" if delta else None)),
        },
    }


def _llm_rationale(user_id: int, plan: dict, notes: str) -> str:
    question = (
        "The deterministic plan is already built. In 120 words, explain it "
        "to the athlete in Italian, honouring these constraints:\n"
        f"{notes}\n"
        "Do not invent sessions. Do not change structure. "
        f"Headline: {json.dumps(plan.get('headline'), default=str)}"
    )
    try:
        return _ask(user_id, question)
    except Exception:
        return ""


def _ask(user_id: int, question: str) -> str:
    return coach._complete(user_id, question)


def llm_patches(user_id: int, plan_id: str, notes: str) -> list[dict]:
    """Ask the model for closed-set patches; apply only those that validate."""
    plan = handle_get_plan(user_id, plan_id)
    if not plan or not config.coach_enabled():
        return []
    question = (
        "Propose at most 3 adjustments as a JSON list of operations. "
        "Each item is one of: "
        '{"op":"move","id":"...","date":"YYYY-MM-DD"}, '
        '{"op":"swap","a":"...","b":"..."}, '
        '{"op":"ease","id":"..."}, {"op":"rest","id":"..."}. '
        "Use only session ids from this plan. Constraints:\n"
        f"{notes}\n\nSessions:\n"
        + json.dumps([{"id": s["id"], "date": s["date"], "kind": s["kind"]}
                      for s in (plan.get("sessions") or [])[:40]], default=str)
        + "\nReply with JSON only."
    )
    try:
        text = _ask(user_id, question)
    except Exception:
        return []
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end < 0:
        return []
    try:
        ops = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []
    applied = []
    for op in ops[:3]:
        if not isinstance(op, dict):
            continue
        try:
            patch_plan(user_id, plan_id, op)
            applied.append(op)
        except Exception:
            continue
    return applied


def templates(user_id: int, paces: dict | None = None) -> list[dict]:
    built = library.catalogue(paces)
    with db.store() as handle:
        custom = handle.list_templates(user_id)
    return [{"source": "builtin", **w} for w in built] + [
        {"source": "user", **t} for t in custom]


def save_template(user_id: int, payload: dict) -> dict:
    workout = validate(payload.get("workout") or payload)
    tmpl = {
        "id": payload.get("id") or str(uuid4()),
        "name": workout["name"],
        "workout": workout,
    }
    with db.store() as handle:
        return handle.upsert_template(user_id, tmpl)
