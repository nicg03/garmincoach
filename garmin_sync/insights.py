"""Compare completed quality work against planned pace targets.

Pace Insights (Runna-style): report the delta and recommend a shift.
Never change future paces until the athlete accepts.
"""
from __future__ import annotations

from statistics import mean, median

from . import workout_dsl
from .performance import format_pace, _num, _r, _shift_pace_s
from .workout_dsl import clone, describe, pace_to_seconds

# Interval steps shorter than this are strides, not quality work.
_MIN_WORK_S = 90
_MIN_WORK_M = 300
# Need this many same-direction quality days before we suggest a change.
_MIN_SAMPLES = 3
_MAX_SHIFT_S = 15
_LOOKBACK = 8


def _walk_steps(workout: dict):
    yield from workout_dsl._walk((workout or {}).get("steps") or [])


def _step_metres(step: dict) -> float | None:
    dur = step.get("duration") or {}
    if dur.get("type") != "distance":
        return None
    value = _num(dur.get("value"))
    if not value:
        return None
    return value * 1000 if dur.get("unit") == "km" else value


def _step_seconds(step: dict) -> float | None:
    dur = step.get("duration") or {}
    if dur.get("type") != "time":
        return None
    value = _num(dur.get("value"))
    if not value:
        return None
    unit = dur.get("unit") or "min"
    if unit in ("min", "m", "minutes"):
        return value * 60
    if unit in ("h", "hr", "hours"):
        return value * 3600
    return value


def _is_quality_work(step: dict) -> bool:
    if (step.get("intensity") or "") != "interval":
        return False
    secs = _step_seconds(step)
    metres = _step_metres(step)
    if secs is not None and secs < _MIN_WORK_S:
        return False
    if metres is not None and metres < _MIN_WORK_M:
        return False
    if secs is None and metres is None:
        return False
    target = step.get("target") or {}
    return target.get("type") == "pace" and bool(
        pace_to_seconds(target.get("low")) or pace_to_seconds(target.get("high")))


def work_target(workout: dict | None) -> dict | None:
    """Mid / bounds of planned quality paces, in seconds per km."""
    lows, highs, mids = [], [], []
    for _times, step in _walk_steps(workout or {}):
        if not _is_quality_work(step):
            continue
        target = step.get("target") or {}
        slow = pace_to_seconds(target.get("low"))
        fast = pace_to_seconds(target.get("high"))
        if slow:
            lows.append(slow)
        if fast:
            highs.append(fast)
        if slow and fast:
            mids.append((slow + fast) / 2)
        elif slow or fast:
            mids.append(slow or fast)
    if not mids:
        return None
    mid = mean(mids)
    low = mean(lows) if lows else mid
    high = mean(highs) if highs else mid
    # low = slower (more s/km), high = faster (fewer s/km)
    if low < high:
        low, high = high, low
    return {
        "mid_s": _r(mid),
        "low_s": _r(low),
        "high_s": _r(high),
        "mid": format_pace(mid),
        "low": format_pace(low),
        "high": format_pace(high),
        "range": f"{format_pace(high)}–{format_pace(low)}",
    }


def _split_pace_s(split: dict) -> float | None:
    metres = _num(split.get("distance_m"))
    seconds = _num(split.get("duration_s"))
    if not metres or not seconds or metres < 200:
        return None
    return seconds / (metres / 1000)


def _activity_pace_s(activity: dict | None) -> float | None:
    if not activity:
        return None
    metres = _num(activity.get("distance_m"))
    seconds = _num(activity.get("duration_s"))
    if not metres or not seconds or metres < 400:
        return None
    return seconds / (metres / 1000)


def actual_work_pace(activity: dict | None, target: dict) -> tuple[float | None, str]:
    """Best estimate of quality-work pace. Prefer splits near the target."""
    if not activity:
        return None, "none"
    high_s = target.get("high_s") or target.get("mid_s")
    low_s = target.get("low_s") or target.get("mid_s")
    mid_s = target.get("mid_s")
    work = []
    for split in activity.get("splits") or []:
        pace = _split_pace_s(split)
        if pace is None or mid_s is None:
            continue
        # Near the work band, not the easy jog between reps.
        if pace <= (low_s or mid_s) * 1.12 and pace >= (high_s or mid_s) * 0.88:
            work.append(pace)
    if work:
        return mean(work), "splits"
    overall = _activity_pace_s(activity)
    if overall is None:
        return None, "none"
    return overall, "overall"


def _status(actual_s: float, target: dict) -> str:
    high_s = target.get("high_s")
    low_s = target.get("low_s")
    mid_s = target.get("mid_s") or actual_s
    if high_s and actual_s < high_s:
        return "too_fast"
    if low_s and actual_s > low_s:
        return "too_slow"
    if high_s is None and low_s is None:
        if actual_s < mid_s * 0.98:
            return "too_fast"
        if actual_s > mid_s * 1.02:
            return "too_slow"
    return "on_target"


def review_session(session: dict, activity: dict | None) -> dict | None:
    """One completed quality day: target vs actual."""
    workout = session.get("workout") or {}
    target = work_target(workout)
    if not target:
        return None
    actual_s, source = actual_work_pace(activity, target)
    if actual_s is None:
        return None
    mid_s = target["mid_s"]
    delta_s = actual_s - mid_s
    delta_pct = 100 * delta_s / mid_s if mid_s else 0
    status = _status(actual_s, target)
    return {
        "session_id": session.get("id"),
        "date": session.get("date"),
        "name": workout.get("name") or session.get("kind"),
        "kind": session.get("kind") or workout.get("kind"),
        "target": target["range"],
        "target_mid": target["mid"],
        "target_mid_s": target["mid_s"],
        "actual": format_pace(actual_s),
        "actual_s": _r(actual_s),
        "delta_s": _r(delta_s),
        "delta_pct": _r(delta_pct, 1),
        "status": status,
        "source": source,
        "confidence": "high" if source == "splits" else "low",
        "activity_id": (activity or {}).get("id"),
    }


def _round_shift(delta_s: float) -> int:
    stepped = int(round(delta_s / 5.0) * 5)
    return max(-_MAX_SHIFT_S, min(_MAX_SHIFT_S, stepped))


def _recommend(reviews: list[dict], dismissed: str | None) -> dict | None:
    usable = [r for r in reviews if r.get("confidence") == "high"]
    if len(usable) < _MIN_SAMPLES:
        return None
    recent = usable[-_MIN_SAMPLES:]
    faster = [r for r in recent if r["status"] == "too_fast"]
    slower = [r for r in recent if r["status"] == "too_slow"]
    if len(faster) >= _MIN_SAMPLES:
        direction = "faster"
        sample = faster
    elif len(slower) >= _MIN_SAMPLES:
        direction = "slower"
        sample = slower
    else:
        return None
    raw = median(r["delta_s"] for r in sample)
    shift_s = _round_shift(raw)
    if shift_s == 0:
        return None
    # faster than target → negative delta_s → we subtract seconds from future paces
    if direction == "faster" and shift_s > 0:
        shift_s = -abs(shift_s)
    if direction == "slower" and shift_s < 0:
        shift_s = abs(shift_s)
    vdot_delta = int(round(-shift_s / 3.0))
    if vdot_delta == 0:
        vdot_delta = 1 if direction == "faster" else -1
    ids = [str(r.get("session_id") or r.get("date")) for r in recent]
    fingerprint = f"{direction}:{','.join(ids)}"
    if dismissed and dismissed == fingerprint:
        return None
    seconds = abs(shift_s)
    if direction == "faster":
        summary = (
            f"The last {len(recent)} quality sessions were faster than the "
            f"planned range. Accept to tighten targets by {seconds} s/km "
            f"(about VDOT +{vdot_delta})."
        )
    else:
        summary = (
            f"The last {len(recent)} quality sessions were slower than the "
            f"planned range. Accept to ease targets by {seconds} s/km "
            f"(about VDOT {vdot_delta})."
        )
    return {
        "direction": direction,
        "shift_s": shift_s,
        "vdot_delta": vdot_delta,
        "fingerprint": fingerprint,
        "summary": summary,
        "n": len(recent),
    }


def analyze(plan: dict | None, activities: list[dict],
            profile: dict | None = None) -> dict:
    """Reviews + optional recommendation for the active plan."""
    by_id = {a.get("id"): a for a in activities if a.get("id") is not None}
    by_day: dict[str, list] = {}
    for a in activities:
        day = (a.get("start") or "")[:10]
        if day:
            by_day.setdefault(day, []).append(a)

    reviews = []
    for session in (plan or {}).get("sessions") or []:
        if session.get("state") != "completed":
            continue
        activity = by_id.get(session.get("activity_id"))
        if activity is None:
            from .planner import match_activity
            activity = match_activity(session, by_day.get(session.get("date") or "", []))
        review = review_session(session, activity)
        if review:
            reviews.append(review)
    reviews.sort(key=lambda r: r.get("date") or "")
    reviews = reviews[-_LOOKBACK:]
    profile = profile or {}
    recommendation = _recommend(reviews, profile.get("insights_dismissed"))
    return {
        "reviews": reviews,
        "recommendation": recommendation,
        "offset_s": profile.get("pace_offset_s") or 0,
        "vdot_override": profile.get("vdot"),
    }


def shift_target(target: dict | None, delta_s: float) -> dict | None:
    if not target or target.get("type") != "pace":
        return target
    out = dict(target)
    for key in ("low", "high"):
        secs = pace_to_seconds(target.get(key))
        if secs:
            out[key] = format_pace(_shift_pace_s(secs, delta_s))
    return out


def shift_workout(workout: dict, delta_s: float) -> dict:
    """Move every pace target, then rewrite the human description."""
    if not delta_s:
        return workout
    out = clone(workout)

    def walk(steps: list) -> list:
        moved = []
        for step in steps:
            if step.get("kind") == "repeat":
                node = dict(step)
                node["steps"] = walk(step.get("steps") or [])
                moved.append(node)
                continue
            node = dict(step)
            if node.get("target"):
                node["target"] = shift_target(node["target"], delta_s)
            moved.append(node)
        return moved

    out["steps"] = walk(out.get("steps") or [])
    work = work_target(out)
    if work and work.get("range"):
        targets = dict(out.get("targets") or {})
        targets["work"] = f"{work['range']}/km"
        out["targets"] = targets
    out.pop("description", None)
    out["description"] = describe(out)
    return out


def shift_paces(paces: dict | None, delta_s: float) -> dict:
    """Apply a seconds/km offset to a training_paces() payload."""
    if not paces or not delta_s:
        return paces or {}
    out = dict(paces)
    bands = {}
    for key, band in (paces.get("bands") or {}).items():
        mid_s = band.get("mid_s")
        if mid_s is None:
            mid_s = pace_to_seconds(band.get("mid"))
        if mid_s is None:
            bands[key] = dict(band)
            continue
        pad = abs((band.get("low_s") or mid_s) - mid_s) or 4
        new_mid = _shift_pace_s(mid_s, delta_s)
        slow = _shift_pace_s(new_mid, pad)
        fast = _shift_pace_s(new_mid, -pad)
        bands[key] = {
            "low": format_pace(slow),
            "high": format_pace(fast),
            "mid": format_pace(new_mid),
            "low_s": _r(slow),
            "high_s": _r(fast),
            "mid_s": _r(new_mid),
        }
        out[key] = bands[key]["mid"]
        if key == "easy":
            out["easy_range"] = f"{bands[key]['high']}–{bands[key]['low']}"
    out["bands"] = bands
    if out.get("goal"):
        goal_s = pace_to_seconds(out["goal"])
        if goal_s:
            out["goal"] = format_pace(_shift_pace_s(goal_s, delta_s))
    note = out.get("note") or ""
    extra = f"Adjusted {int(delta_s):+d} s/km from Pace Insights."
    out["note"] = f"{note} {extra}".strip() if note else extra
    return out


def restamp_plan(plan: dict, delta_s: float, today: str) -> dict:
    """Shift paces on future sessions that are still planned or queued."""
    if not delta_s:
        return plan
    updated = dict(plan)
    sessions = []
    for session in plan.get("sessions") or []:
        state = session.get("state") or "planned"
        day = session.get("date") or ""
        if state in ("completed", "skipped") or day < today:
            sessions.append(session)
            continue
        if (session.get("kind") or "") in ("rest", "strength", "bike", "swim"):
            sessions.append(session)
            continue
        session = dict(session)
        workout = session.get("workout") or {}
        session["workout"] = shift_workout(workout, delta_s)
        session["description"] = session["workout"].get("description")
        session["purpose"] = session["workout"].get("purpose") or session.get("purpose")
        session["revision"] = int(session.get("revision") or 1) + 1
        sessions.append(session)
    updated["sessions"] = sessions
    headline = dict(updated.get("headline") or {})
    if headline.get("paces"):
        headline["paces"] = shift_paces(headline["paces"], delta_s)
        if headline.get("vdot") is not None:
            try:
                headline["vdot"] = round(float(headline["vdot"]) + (-delta_s / 3.0), 1)
            except (TypeError, ValueError):
                pass
    updated["headline"] = headline
    return updated
