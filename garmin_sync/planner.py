"""
Deterministic periodization: given a race and an athlete, emit a plan.

Works backwards from the A-race. Mesocycles and session types change with
distance (5k / 10k / half / marathon). Weekly kilometres ramp from recent
volume. Sessions are composed (WU / set / CD) with Daniels pace ranges.

The LLM is not invited here. It may later propose patches from a closed
set of operations; those go through `apply_patch`.
"""
from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

from . import metrics, sessions, workout_dsl
from .performance import _is_run, _num, build as build_performance, training_paces

PHASES = ("base", "build", "peak", "taper")
PATCH_OPS = ("move", "swap", "ease", "rest", "skip", "replace")

CYCLE = (1.0, 1.06, 1.10, 0.85)
MAX_WEEKLY_RAMP = 0.08
MAX_ACWR = 1.25
IDEAL_WEEKS = {"5k": 10, "10k": 12, "half": 14, "marathon": 18}

HARD = sessions.HARD


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _iso(day: date | str) -> str:
    return day if isinstance(day, str) else day.isoformat()


def _parse(day: str) -> date:
    return date.fromisoformat(day[:10])


def race_family(distance_m) -> str:
    dist = _num(distance_m) or 21097
    if dist <= 6000:
        return "5k"
    if dist <= 12000:
        return "10k"
    if dist <= 25000:
        return "half"
    return "marathon"


def _availability(athlete: dict) -> dict[int, dict]:
    """weekday 0=Mon … 6=Sun → {minutes, sports}."""
    raw = (athlete or {}).get("availability") or {}
    names = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    out = {}
    for i, name in enumerate(names):
        slot = raw.get(name) or raw.get(str(i)) or {}
        minutes = slot.get("minutes")
        if minutes is None:
            minutes = 90 if i in (5, 6) else (0 if i == 0 else 60)
        sports = slot.get("sports") or ["running"]
        if minutes and sports:
            out[i] = {"minutes": int(minutes), "sports": list(sports)}
    if not out:
        out = {1: {"minutes": 50, "sports": ["running"]},
               3: {"minutes": 50, "sports": ["running"]},
               5: {"minutes": 90, "sports": ["running"]}}
    return out


def recent_weekly_km(activities: list[dict], today: date, weeks: int = 4) -> float:
    cutoff = today - timedelta(days=7 * weeks)
    by_week: dict[date, float] = {}
    for a in activities or []:
        if not _is_run(a):
            continue
        start = (a.get("start") or "")[:10]
        if len(start) < 10:
            continue
        day = _parse(start)
        if day < cutoff or day >= today:
            continue
        km = (_num(a.get("distance_m")) or 0) / 1000
        key = _monday(day)
        by_week[key] = by_week.get(key, 0.0) + km
    if not by_week:
        return 25.0
    values = list(by_week.values())
    values.sort()
    return values[len(values) // 2]


def _phase_for(week_index: int, weeks: int, family: str) -> str:
    if weeks <= 1:
        return "taper"
    taper = {"5k": 1, "10k": 2, "half": 2, "marathon": 3}[family]
    peak = {"5k": 2, "10k": 2, "half": 2, "marathon": 3}[family]
    if weeks < 8:
        taper = 1
        peak = min(peak, max(1, weeks - 2))
    elif weeks < 12:
        taper = min(taper, 2)
        peak = min(peak, 2)
    taper = min(taper, max(1, weeks - 1))
    peak = min(peak, max(0, weeks - taper - 1))
    remaining = weeks - taper - peak
    build = max(1, round(remaining * 0.45)) if remaining else 0
    build = min(build, remaining)
    if week_index >= weeks - taper:
        return "taper"
    if week_index >= weeks - taper - peak:
        return "peak"
    if week_index >= weeks - taper - peak - build:
        return "build"
    return "base"


def _adjacent(a: int, b: int) -> bool:
    return abs(a - b) == 1 or {a, b} == {0, 6}


def _pick_day(candidates: list[int], avoid: list[int]) -> int | None:
    if not candidates:
        return None
    scored = []
    for day in candidates:
        penalty = 0
        for other in avoid:
            if day == other:
                penalty += 5
            elif _adjacent(day, other):
                penalty += 2
        scored.append((penalty, day))
    scored.sort()
    return scored[0][1]


def _qualities(family: str, phase: str, week_index: int, n_days: int) -> tuple:
    """(long_kind, primary, secondary). secondary may be None."""
    long_kind = "long"
    primary = secondary = None
    if family == "5k":
        if phase == "base":
            primary = "reps"
        elif phase == "build":
            primary = "vo2"
            secondary = "tempo" if n_days >= 5 else None
        elif phase == "peak":
            primary = "time_trial" if week_index % 3 == 2 else "vo2"
            secondary = "mix" if n_days >= 5 else None
        else:
            primary = "strides"
            long_kind = "easy"
    elif family == "10k":
        if phase == "base":
            primary = "tempo"
        elif phase == "build":
            primary = "cruise"
            secondary = "vo2" if n_days >= 5 else None
        elif phase == "peak":
            primary = "race_pace"
            secondary = "vo2" if n_days >= 5 else None
        else:
            primary = "strides"
            long_kind = "easy"
    elif family == "half":
        if phase == "base":
            primary = "tempo"
            secondary = "medium_long" if n_days >= 5 else None
        elif phase == "build":
            primary = "cruise"
            secondary = "medium_long" if n_days >= 4 else None
        elif phase == "peak":
            primary = "race_pace"
            secondary = "vo2" if n_days >= 5 else None
            long_kind = "progression_long"
        else:
            primary = "strides"
            long_kind = "easy"
    else:
        if phase == "base":
            primary = "tempo"
            secondary = "medium_long" if n_days >= 5 else None
        elif phase == "build":
            primary = "cruise"
            secondary = "medium_long" if n_days >= 4 else None
        elif phase == "peak":
            primary = "vo2" if week_index % 2 else "cruise"
            secondary = "medium_long" if n_days >= 5 else None
            long_kind = "mp_long"
        else:
            primary = "strides"
            long_kind = "easy"
    if n_days <= 3:
        secondary = None
    return long_kind, primary, secondary


def _week_kinds(family: str, phase: str, days_open: list[int], extras: set[str],
                week_index: int) -> dict[int, str]:
    days_open = sorted(days_open)
    assigned: dict[int, str] = {}
    if not days_open:
        return assigned
    n = len(days_open)
    long_kind, primary, secondary = _qualities(family, phase, week_index, n)

    long_day = days_open[-1]
    assigned[long_day] = long_kind
    remaining = [d for d in days_open if d not in assigned]

    q_day = _pick_day(remaining, [long_day])
    if primary and q_day is not None:
        assigned[q_day] = primary
        remaining = [d for d in remaining if d != q_day]

    if secondary and remaining:
        s_day = _pick_day(remaining, [long_day, q_day] if q_day is not None else [long_day])
        if s_day is not None:
            assigned[s_day] = secondary
            remaining = [d for d in remaining if d != s_day]

    # Recovery the day after a hard session when we still have a free day.
    hard_days = [d for d, k in assigned.items() if k in HARD]
    if remaining and hard_days:
        rec = None
        for d in remaining:
            if any(_adjacent(d, h) for h in hard_days):
                rec = d
                break
        if rec is not None:
            assigned[rec] = "recovery"
            remaining = [d for d in remaining if d != rec]

    if remaining and extras:
        extra = "strength" if "strength" in extras else next(iter(extras))
        # Never glue strength onto a VO2/reps day.
        avoid = [d for d, k in assigned.items() if k in ("vo2", "reps", "time_trial")]
        e_day = _pick_day(remaining, avoid)
        if e_day is not None:
            assigned[e_day] = extra
            remaining = [d for d in remaining if d != e_day]

    for i, d in enumerate(remaining):
        assigned[d] = "easy"
        if phase in ("base", "taper") and i == 0:
            assigned[d] = "easy"  # strides added via compose flag
    return assigned


def _session(day: date, kind: str, minutes: int, paces: dict, extras: bool,
             phase: str, weekly_km: float, week_index: int, family: str,
             race: dict, strides: bool = False) -> dict:
    workout = sessions.compose(
        kind, paces,
        minutes=minutes,
        weekly_km=weekly_km,
        phase=phase,
        week_index=week_index,
        race_distance_m=race.get("distance_m"),
        strides=strides,
        family=family,
        name=race.get("name"),
    )
    return {
        "id": str(uuid4()),
        "date": day.isoformat(),
        "sport": workout.get("sport") or "running",
        "kind": workout.get("kind") or kind,
        "phase": phase,
        "workout": workout,
        "purpose": workout.get("purpose"),
        "description": workout.get("description") or workout_dsl.describe(workout),
        "distance_km": workout.get("distance_km"),
        "est_load": workout.get("est_load") or 0,
        "state": "planned",
    }


def _projected(sessions_list: list[dict], history_loads: list[float]) -> list[dict]:
    """CTL/ATL from real history, then the plan's estimated loads."""
    seed = list(history_loads[-60:]) if history_loads else [0.0]
    by_day: dict[str, float] = {}
    for s in sessions_list:
        by_day[s["date"]] = by_day.get(s["date"], 0) + (s.get("est_load") or 0)
    if not sessions_list:
        return []
    start = _parse(sessions_list[0]["date"])
    end = _parse(sessions_list[-1]["date"])
    loads = list(seed)
    day = start
    while day <= end:
        loads.append(by_day.get(day.isoformat(), 0.0))
        day += timedelta(days=1)
    atl = metrics.ema(loads, metrics.ATL_SPAN)
    ctl = metrics.ema(loads, metrics.CTL_SPAN)
    cut = len(seed)
    out = []
    day = start
    i = 0
    while day <= end:
        j = cut + i
        out.append({
            "date": day.isoformat(),
            "load": metrics._r(loads[j]),
            "atl": metrics._r(atl[j]),
            "ctl": metrics._r(ctl[j]),
            "form": metrics._r(ctl[j] - atl[j]),
        })
        day += timedelta(days=1)
        i += 1
    return out


def _taper_factor(family: str, week_index: int, weeks: int, phase: str) -> float:
    if phase != "taper":
        return 1.0
    remaining = weeks - week_index
    if family == "marathon":
        return {3: 0.80, 2: 0.65, 1: 0.50}.get(remaining, 0.70)
    if remaining <= 1:
        return 0.55
    return 0.75


def generate(race: dict, athlete: dict | None, activities: list[dict],
             days: list[dict], today: str | None = None,
             extras: list[str] | None = None,
             races: list[dict] | None = None) -> dict:
    """Build a full plan from today to the race date."""
    today_d = _parse(today) if today else date.today()
    race_d = _parse(race["date"])
    if race_d <= today_d:
        raise ValueError("The race has to be in the future.")

    family = race_family(race.get("distance_m"))
    start = _monday(today_d)
    if start < today_d:
        start = today_d
    weeks = max(1, ((_monday(race_d) - _monday(start)).days // 7) + 1)
    avail = _availability(athlete or {})
    extras_set = set(extras or (athlete or {}).get("extras") or [])
    perf = build_performance(activities)
    override = _num((athlete or {}).get("vdot"))
    vdot = override or perf.get("vdot")
    paces = training_paces(
        vdot, perf.get("critical_speed"),
        race.get("goal_time"), race.get("distance_m"))
    offset = _num((athlete or {}).get("pace_offset_s"))
    if offset:
        from .insights import shift_paces
        paces = shift_paces(paces, offset)

    built = metrics.build(days, activities)
    history_loads = [row.get("load") or 0 for row in built.get("training") or []]
    current_ctl = (built.get("headline") or {}).get("ctl") or 20
    current_km = recent_weekly_km(activities, today_d)
    peak_cap = current_km * {"5k": 1.25, "10k": 1.35, "half": 1.5, "marathon": 1.7}[family]
    sessions_list: list[dict] = []
    week_rows = []
    trend_km = current_km
    peak_km = current_km

    for w in range(weeks):
        monday = _monday(start) + timedelta(weeks=w)
        phase = _phase_for(w, weeks, family)
        cycle = CYCLE[w % 4]
        if phase == "taper":
            week_km = peak_km * _taper_factor(family, w, weeks, phase)
        else:
            if cycle >= 1:
                trend_km = min(trend_km * (1 + MAX_WEEKLY_RAMP), peak_cap)
            week_km = trend_km * cycle
            if phase == "base":
                week_km *= 0.95
            peak_km = max(peak_km, week_km)
        week_km = max(week_km, min(16.0, current_km * 0.7))

        kinds = _week_kinds(family, phase, list(avail), extras_set, w)
        week_sessions = []
        for weekday, kind in kinds.items():
            day = monday + timedelta(days=weekday)
            if day < today_d or day > race_d:
                continue
            minutes = avail.get(weekday, {}).get("minutes") or 45
            if phase == "taper":
                minutes = min(minutes, 55 if kind in ("long", "mp_long", "progression_long") else 45)
            strides = kind == "easy" and phase in ("base", "taper") and weekday == min(
                (d for d, k in kinds.items() if k == "easy"), default=-1)
            week_sessions.append(
                _session(day, kind, minutes, paces, bool(extras_set), phase,
                         week_km, w, family, race, strides=strides))

        raw_km = sum(s.get("distance_km") or 0 for s in week_sessions) or 1
        # Easy days absorb leftover (or surplus) kilometre budget.
        scale = week_km / raw_km
        if abs(scale - 1) > 0.12:
            for s in week_sessions:
                if s["kind"] in ("easy", "recovery", "medium_long") and scale < 1:
                    mins = max(25, int((avail.get(_parse(s["date"]).weekday(), {})
                                        .get("minutes") or 40) * scale))
                    rebuilt = _session(
                        _parse(s["date"]), s["kind"], mins, paces, False, phase,
                        week_km, w, family, race)
                    rebuilt["id"] = s["id"]
                    idx = week_sessions.index(s)
                    week_sessions[idx] = rebuilt

        sessions_list.extend(week_sessions)
        week_rows.append({
            "week": f"{monday.isocalendar()[0]}-W{monday.isocalendar()[1]:02d}",
            "start": monday.isoformat(),
            "phase": phase,
            "target_km": round(week_km, 1),
            "target_load": round(sum(s["est_load"] for s in week_sessions), 1),
            "sessions": len(week_sessions),
        })

    # B/C races inside the block become a tune-up, replacing that day's quality.
    by_date = {s["date"]: s for s in sessions_list}
    for other in races or []:
        if other.get("id") == race.get("id"):
            continue
        if (other.get("priority") or "A") == "A":
            continue
        day = (other.get("date") or "")[:10]
        if day not in by_date or day == race_d.isoformat():
            continue
        existing = by_date[day]
        if existing.get("kind") == "race":
            continue
        tune = _session(
            _parse(day), "time_trial", 50, paces, False,
            existing.get("phase") or "build", current_km, 0, family, other)
        tune["id"] = existing["id"]
        tune["kind"] = "time_trial"
        sessions_list = [tune if s["id"] == existing["id"] else s for s in sessions_list]

    sessions_list.append(_session(
        race_d, "race", 90, paces, False, "taper", current_km, weeks, family, race))
    sessions_list.sort(key=lambda s: s["date"])

    series = _projected(sessions_list, history_loads)
    for _ in range(3):
        over = next((p for p in series if p.get("ctl") and p["ctl"] > 0
                     and (p.get("atl") or 0) / p["ctl"] > MAX_ACWR), None)
        if not over:
            break
        cut = over["date"]
        changed = False
        for i, s in enumerate(sessions_list):
            if s["date"] >= cut and s["kind"] in ("easy", "recovery", "medium_long"):
                mins = 30
                rebuilt = _session(
                    _parse(s["date"]), "recovery" if s["kind"] == "recovery" else "easy",
                    mins, paces, False, s.get("phase") or "build",
                    current_km, 0, family, race)
                rebuilt["id"] = s["id"]
                sessions_list[i] = rebuilt
                changed = True
        if not changed:
            break
        series = _projected(sessions_list, history_loads)

    acwr = None
    if series:
        last = series[-1]
        if last.get("ctl"):
            acwr = metrics._r((last.get("atl") or 0) / last["ctl"], 2)

    return {
        "race_id": race.get("id"),
        "from": start.isoformat(),
        "to": race_d.isoformat(),
        "family": family,
        "weeks": week_rows,
        "sessions": sessions_list,
        "projected": series,
        "headline": {
            "weeks": weeks,
            "sessions": len(sessions_list),
            "vdot": vdot,
            "paces": paces,
            "family": family,
            "weekly_km_now": round(current_km, 1),
            "weekly_km_peak": round(peak_km, 1),
            "acwr_at_race": acwr,
            "ctl_now": current_ctl,
            "ctl_at_race": series[-1]["ctl"] if series else None,
            "ideal_weeks": IDEAL_WEEKS[family],
            "short_block": weeks < IDEAL_WEEKS[family] - 2,
        },
        "rationale": _rationale(weeks, week_rows, paces, race, family, current_km),
    }


def _rationale(weeks: int, week_rows: list[dict], paces: dict, race: dict,
               family: str, current_km: float) -> str:
    phases = []
    for name in PHASES:
        n = sum(1 for w in week_rows if w["phase"] == name)
        if n:
            phases.append(f"{name} {n}w")
    easy = (paces.get("bands") or {}).get("easy") or {}
    if easy.get("low") and easy.get("high"):
        easy_rng = f"{easy['high']}–{easy['low']}/km"
    elif paces.get("easy"):
        easy_rng = f"{paces['easy']}/km"
    else:
        easy_rng = "once we have a recent 5k-ish effort"
    t = paces.get("threshold")
    t_txt = f"{t}/km" if t else "threshold once VDOT is known"
    goal = race.get("goal_time") or "current fitness, not a dream time"
    ideal = IDEAL_WEEKS[family]
    short = ""
    if weeks < ideal - 2:
        short = (
            f" This block is {weeks} weeks; {ideal} is the usual minimum for a "
            f"{family}, so the base is compressed — don't jump the long run."
        )
    long_peak = max((w.get("target_km") or 0) for w in week_rows) if week_rows else current_km
    return (
        f"{weeks} weeks to {race.get('name') or 'the race'} ({family}), "
        f"built from ~{current_km:.0f} km/week toward a peak near {long_peak:.0f} km. "
        f"Mesocycles: {', '.join(phases)}. Easy running {easy_rng}; threshold around {t_txt}. "
        f"Paces come from current VDOT, aiming at {goal}. "
        f"Weekly volume ramps at most 8% with a 3:1 load cycle; the long run stays "
        f"under 30% of the week and 150 minutes.{short}"
    )


def apply_patch(plan: dict, op: dict) -> dict:
    """Apply one closed-set operation and return a new plan dict."""
    kind = op.get("op")
    if kind not in PATCH_OPS:
        raise ValueError(f"Unknown patch {kind!r}. Allowed: {PATCH_OPS}")
    session_list = list(plan.get("sessions") or [])
    by_id = {s["id"]: s for s in session_list}
    paces = (plan.get("headline") or {}).get("paces") or {}

    if kind == "move":
        session = by_id.get(op.get("id"))
        dest = op.get("date")
        if not session or not dest:
            raise ValueError("move needs id and date")
        session = dict(session)
        session["date"] = dest
        session_list = [session if s["id"] == session["id"] else s for s in session_list]
    elif kind == "swap":
        a, b = by_id.get(op.get("a")), by_id.get(op.get("b"))
        if not a or not b:
            raise ValueError("swap needs two session ids")
        da, db = a["date"], b["date"]
        a, b = dict(a), dict(b)
        a["date"], b["date"] = db, da
        session_list = [a if s["id"] == a["id"] else (b if s["id"] == b["id"] else s)
                        for s in session_list]
    elif kind == "ease":
        session = by_id.get(op.get("id"))
        if not session:
            raise ValueError("ease needs id")
        easy = sessions.compose("easy", paces, minutes=35)
        session = dict(session)
        session["workout"] = easy
        session["kind"] = "easy"
        session["est_load"] = easy.get("est_load") or 20
        session["purpose"] = easy.get("purpose")
        session["description"] = easy.get("description")
        session["distance_km"] = easy.get("distance_km")
        session_list = [session if s["id"] == session["id"] else s for s in session_list]
    elif kind in ("rest", "skip"):
        session = by_id.get(op.get("id"))
        if not session:
            raise ValueError(f"{kind} needs id")
        rest = sessions.compose("rest", paces)
        session = dict(session)
        session["workout"] = rest
        session["kind"] = "rest"
        session["est_load"] = 0
        session["purpose"] = "Full rest."
        session["description"] = "Rest"
        session["distance_km"] = 0
        if kind == "skip":
            session["state"] = "skipped"
        session_list = [session if s["id"] == session["id"] else s for s in session_list]
    elif kind == "replace":
        session = by_id.get(op.get("id"))
        workout = op.get("workout")
        if not session or not workout:
            raise ValueError("replace needs id and workout")
        workout = workout_dsl.validate(workout)
        session = dict(session)
        session["workout"] = workout
        session["kind"] = workout.get("kind") or session["kind"]
        session["sport"] = workout.get("sport") or session["sport"]
        session["est_load"] = workout.get("est_load") or 0
        session["purpose"] = workout.get("purpose")
        session["description"] = workout.get("description") or workout_dsl.describe(workout)
        session["distance_km"] = workout.get("distance_km")
        session_list = [session if s["id"] == session["id"] else s for s in session_list]

    session_list.sort(key=lambda s: s["date"])
    plan = dict(plan)
    plan["sessions"] = session_list
    return plan


def match_activity(session: dict, activities: list[dict]) -> dict | None:
    """Best activity on the same day and a compatible sport, if any."""
    day = session.get("date")
    sport = session.get("sport") or "running"
    candidates = []
    for a in activities:
        if (a.get("start") or "")[:10] != day:
            continue
        typ = (a.get("type") or "").lower()
        if sport == "running" and not any(t in typ for t in ("run", "trail", "track")):
            continue
        if sport == "cycling" and "cycl" not in typ and "bike" not in typ and "ride" not in typ:
            continue
        if sport == "swimming" and "swim" not in typ:
            continue
        if sport == "strength" and "strength" not in typ and "yoga" not in typ:
            continue
        candidates.append(a)
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.get("duration_s") or 0)
