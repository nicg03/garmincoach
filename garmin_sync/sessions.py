"""
Compose structured running sessions: WU → set → CD, with pace ranges.

The planner asks for a kind and a time/km budget. This module applies
Daniels volume caps and emits a workout the DSL and Garmin can both use.
Templates in library.py stay for user-saved sessions and non-running extras.
"""
from __future__ import annotations

from . import library, workout_dsl
from .performance import pace_target
from .workout_dsl import describe, validate

# Work (not WU/CD) as a share of weekly kilometres.
CAP_T = 0.10
CAP_I = 0.08
CAP_R = 0.05
CAP_LONG = 0.30
LONG_MINUTES = 150
I_KM_ABS = 10.0
R_KM_ABS = 8.0

HARD = frozenset({
    "tempo", "cruise", "vo2", "reps", "mix", "race_pace",
    "time_trial", "mp_long", "progression_long",
})


def _easy_s(paces: dict | None) -> float:
    band = ((paces or {}).get("bands") or {}).get("easy") or {}
    return band.get("mid_s") or 330.0


def _t_s(paces: dict | None) -> float:
    band = ((paces or {}).get("bands") or {}).get("threshold") or {}
    return band.get("mid_s") or _easy_s(paces) * 0.85


def _km_from_min(minutes: float, pace_s: float) -> float:
    if not minutes or not pace_s:
        return 0.0
    return (minutes * 60) / pace_s


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _dist(km: float | None = None, metres: int | None = None) -> dict:
    if metres is not None:
        return {"type": "distance", "value": int(metres), "unit": "m"}
    km = round(max(0.2, km or 1), 2)
    if abs(km - round(km, 1)) < 0.001:
        km = round(km, 1)
    metres = int(round(km * 1000))
    if metres % 100 == 0 and metres >= 1000:
        return {"type": "distance", "value": round(metres / 1000, 2), "unit": "km"}
    return {"type": "distance", "value": metres, "unit": "m"}


def _time(minutes: float) -> dict:
    if minutes >= 1 and abs(minutes - round(minutes)) < 0.05:
        return {"type": "time", "value": int(round(minutes)), "unit": "min"}
    return {"type": "time", "value": int(round(minutes * 60)), "unit": "s"}


def _step(intensity: str, duration: dict, target: dict | None = None,
          note: str | None = None) -> dict:
    out = {"kind": "step", "intensity": intensity, "duration": duration}
    if target:
        out["target"] = target
    if note:
        out["note"] = note
    return out


def _repeat(times: int, steps: list[dict]) -> dict:
    return {"kind": "repeat", "times": int(times), "steps": steps}


def _wu_cd(paces: dict | None, wu_km: float = 1.2, cd_km: float = 1.0) -> tuple:
    easy = pace_target(paces, "easy")
    return (
        _step("warmup", _dist(wu_km), easy, "conversational"),
        _step("cooldown", _dist(cd_km), easy, "easy"),
    )


def _finish(name: str, kind: str, steps: list[dict], purpose: str,
            paces: dict | None, work_label: str | None = None) -> dict:
    raw = {
        "name": name[:80],
        "sport": "running",
        "kind": kind,
        "purpose": purpose,
        "steps": steps,
    }
    raw["description"] = describe(raw)
    if work_label:
        band = ((paces or {}).get("bands") or {}).get(work_label) or {}
        rng = f"{band.get('high')}–{band.get('low')}/km" if band.get("low") else None
        if rng:
            raw["targets"] = {"work": rng}
    cleaned = validate(raw)
    # Preserve coaching fields validate already copies; estimate distance.
    metres = 0.0
    for times, step in workout_dsl._walk(cleaned.get("steps") or []):
        dur = step.get("duration") or {}
        if dur.get("type") == "distance":
            value = float(dur.get("value") or 0)
            metres += times * (value * 1000 if dur.get("unit") == "km" else value)
    if metres:
        cleaned["distance_km"] = round(metres / 1000, 2)
    return cleaned


def _weekly(weekly_km: float | None) -> float:
    return max(12.0, float(weekly_km or 40.0))


def _cap_t(weekly_km: float) -> float:
    return max(2.0, CAP_T * weekly_km)


def _cap_i(weekly_km: float) -> float:
    return max(1.6, min(CAP_I * weekly_km, I_KM_ABS))


def _cap_r(weekly_km: float) -> float:
    return max(1.2, min(CAP_R * weekly_km, R_KM_ABS))


def _cap_long(weekly_km: float, paces: dict | None) -> float:
    by_share = CAP_LONG * weekly_km
    by_time = _km_from_min(LONG_MINUTES, _easy_s(paces))
    return max(5.0, min(by_share, by_time, 32.0))


def _budget_km(minutes: float | None, km: float | None, paces: dict | None) -> float:
    if km:
        return float(km)
    return _km_from_min(minutes or 45, _easy_s(paces))


def easy(paces, minutes=None, km=None, strides=False, recovery=False, **_):
    budget = _budget_km(minutes, km, paces)
    if recovery:
        budget = min(budget, max(4.0, budget * 0.7))
        target = pace_target(paces, "recovery") or pace_target(paces, "easy")
        kind = "recovery"
        name = f"Recovery {budget:.0f} km"
        purpose = "Very easy jogging to loosen the legs after a hard day."
    else:
        target = pace_target(paces, "easy")
        kind = "easy"
        name = f"Easy {budget:.1f} km".replace(".0 km", " km")
        purpose = "Aerobic running at conversational effort. Most of the week's kilometres live here."
    steps = [_step("active", _dist(budget), target)]
    if strides and not recovery and budget >= 5:
        steps.append(_repeat(6, [
            _step("interval", _time(20 / 60), pace_target(paces, "rep"), "stride"),
            _step("recovery", _time(1), pace_target(paces, "easy")),
        ]))
        name = name + " + strides"
    return _finish(name, kind, steps, purpose, paces, "recovery" if recovery else "easy")


def medium_long(paces, minutes=None, km=None, weekly_km=None, **_):
    cap = _cap_long(_weekly(weekly_km), paces) * 0.72
    budget = min(_budget_km(minutes, km, paces), cap)
    budget = _clip(budget, min(8.0, cap), cap)
    target = pace_target(paces, "easy")
    purpose = "Pfitzinger's midweek endurance run: longer than an easy day, still conversational."
    steps = [_step("active", _dist(budget), target)]
    return _finish(f"Medium-long {budget:.0f} km", "medium_long", steps, purpose, paces, "easy")


def long_run(paces, minutes=None, km=None, weekly_km=None, **_):
    cap = _cap_long(_weekly(weekly_km), paces)
    budget = min(_budget_km(minutes, km, paces), cap)
    budget = _clip(budget, min(8.0, cap), cap)
    wu_km = 0.8 if budget >= 10 else 0.0
    cd_km = 0.8 if budget >= 10 else 0.0
    main_km = max(4.0, budget - wu_km - cd_km)
    steps = []
    easy = pace_target(paces, "easy")
    if wu_km:
        steps.append(_step("warmup", _dist(wu_km), easy, "settle"))
    steps.append(_step("active", _dist(main_km), easy, "steady"))
    if cd_km:
        steps.append(_step("cooldown", _dist(cd_km), easy, "easy"))
    purpose = "Aerobic endurance. Keep it easy — long is a duration stimulus, not a race."
    return _finish(f"Long {budget:.0f} km", "long", steps, purpose, paces, "easy")


def progression_long(paces, minutes=None, km=None, weekly_km=None, family="half", **_):
    cap = _cap_long(_weekly(weekly_km), paces)
    budget = min(_budget_km(minutes, km, paces), cap)
    budget = _clip(budget, min(8.0, cap), cap)
    quality_km = min(max(1.0, budget * 0.28), _cap_t(_weekly(weekly_km)), budget * 0.4)
    easy_km = max(2.0, budget - quality_km)
    zone = "marathon" if family == "marathon" else "threshold"
    steps = [
        _step("active", _dist(easy_km), pace_target(paces, "easy"), "settle"),
        _step("interval", _dist(quality_km), pace_target(paces, zone), "progress"),
    ]
    purpose = "Long run that finishes at race-relevant pace so the last kilometres teach control, not just time on feet."
    return _finish("Progression long", "long", steps, purpose, paces, zone)


def mp_long(paces, minutes=None, km=None, weekly_km=None, week_index=0, **_):
    cap = _cap_long(_weekly(weekly_km), paces)
    budget = min(_budget_km(minutes, km, paces), cap)
    budget = _clip(budget, min(10.0, cap), cap)
    if budget < 10:
        return long_run(paces, minutes=minutes, km=budget, weekly_km=weekly_km)
    mp = min(max(3.0, 4.0 + 0.4 * week_index), budget * 0.4, 14.0, budget - 4)
    easy_km = max(3.0, budget - mp)
    steps = [
        _step("active", _dist(easy_km * 0.45), pace_target(paces, "easy")),
        _step("interval", _dist(mp), pace_target(paces, "marathon"), "marathon pace"),
        _step("active", _dist(easy_km * 0.55), pace_target(paces, "easy")),
    ]
    purpose = "Marathon-pace work inside a long run — the race-specific session in a marathon or late half block."
    return _finish(f"MP long ({mp:.0f} km M)", "long", steps, purpose, paces, "marathon")


def tempo(paces, minutes=None, km=None, weekly_km=None, phase="build", **_):
    t_s = _t_s(paces)
    work_min = 16 if phase == "base" else (20 if phase != "peak" else 24)
    work_km = min(_km_from_min(work_min, t_s), _cap_t(_weekly(weekly_km)))
    work_min = int(_clip((work_km * t_s) / 60, 12, 30))
    wu, cd = _wu_cd(paces, 1.5, 1.2)
    steps = [
        wu,
        _step("interval", _time(work_min), pace_target(paces, "threshold"), "comfortably hard"),
        cd,
    ]
    purpose = "Lactate threshold: comfortably hard, about the effort you could hold for an hour."
    return _finish(f"Tempo {work_min} min", "tempo", steps, purpose, paces, "threshold")


def cruise(paces, minutes=None, km=None, weekly_km=None, week_index=0, **_):
    cap = _cap_t(_weekly(weekly_km))
    reps = 3 if cap < 6 else (4 if cap < 8 else 5)
    piece = 1600 if cap / reps >= 1.5 else 1000
    while reps * (piece / 1000) > cap and reps > 2:
        reps -= 1
    wu, cd = _wu_cd(paces, 1.5, 1.0)
    rec = _step("recovery", _time(1), pace_target(paces, "easy"), "jog")
    steps = [
        wu,
        _repeat(reps, [
            _step("interval", _dist(metres=piece), pace_target(paces, "threshold")),
            rec,
        ]),
        cd,
    ]
    purpose = "Cruise intervals: threshold pace broken into repeats with a short jog so you can accumulate more T than a single tempo."
    label = f"{reps}×{piece}m T"
    return _finish(label, "tempo", steps, purpose, paces, "threshold")


def vo2(paces, minutes=None, km=None, weekly_km=None, week_index=0, **_):
    cap = _cap_i(_weekly(weekly_km))
    variants = (
        (5, 1000, 120),
        (6, 800, 90),
        (4, 1200, 150),
    )
    reps, piece, rec_s = variants[week_index % 3]
    while reps * (piece / 1000) > cap and reps > 3:
        reps -= 1
    wu, cd = _wu_cd(paces, 1.6, 1.2)
    rec = _step("recovery", {"type": "time", "value": rec_s, "unit": "s"},
                pace_target(paces, "easy"), "jog recovery")
    steps = [
        wu,
        _repeat(reps, [
            _step("interval", _dist(metres=piece), pace_target(paces, "interval")),
            rec,
        ]),
        cd,
    ]
    purpose = "VO2max intervals (3–5 min at I). Jog recovery about as long as the rep so you actually hit VO2max, not just go anaerobic."
    return _finish(f"{reps}×{piece} m I", "interval", steps, purpose, paces, "interval")


def reps(paces, minutes=None, km=None, weekly_km=None, week_index=0, **_):
    cap = _cap_r(_weekly(weekly_km))
    n = 8 if cap < 3.2 else (10 if cap < 4.5 else 12)
    while n * 0.4 > cap and n > 6:
        n -= 1
    wu, cd = _wu_cd(paces, 1.5, 1.0)
    rec = _step("recovery", _time(90 / 60), pace_target(paces, "recovery") or pace_target(paces, "easy"),
                "full recovery")
    steps = [
        wu,
        _repeat(n, [
            _step("interval", _dist(metres=400), pace_target(paces, "rep")),
            rec,
        ]),
        cd,
    ]
    purpose = "Repetition pace: short, fast, full recovery. Economy and speed, not fatigue."
    return _finish(f"{n}×400 m R", "interval", steps, purpose, paces, "rep")


def mix(paces, minutes=None, km=None, weekly_km=None, **_):
    cap = min(_cap_t(_weekly(weekly_km)), 4.8)
    n = 3 if cap < 3.2 else 4
    wu, cd = _wu_cd(paces, 1.2, 1.0)
    steps = [
        wu,
        _repeat(n, [
            _step("interval", _dist(metres=400), pace_target(paces, "threshold")),
            _step("recovery", _dist(metres=400), pace_target(paces, "easy"), "float"),
        ]),
        _step("rest", {"type": "time", "value": 90, "unit": "s"}),
        cd,
    ]
    purpose = "Alternating threshold and easy 400s. Teaches changing gears without a full stop."
    return _finish(f"{n}×(400 T + 400 E)", "tempo", steps, purpose, paces, "threshold")


def race_pace(paces, minutes=None, km=None, weekly_km=None, race_distance_m=None,
              goal_pace=None, **_):
    dist = float(race_distance_m or 10000)
    if dist <= 6000:
        work = min(3.0, _cap_i(_weekly(weekly_km)) + 1)
        zone = "interval"
        label = "5k pace"
    elif dist <= 12000:
        work = min(5.0, _cap_t(_weekly(weekly_km)))
        zone = "threshold"
        label = "10k pace"
    elif dist <= 25000:
        work = min(8.0, _cap_t(_weekly(weekly_km)) * 1.2)
        zone = "threshold"
        label = "half pace"
    else:
        work = min(12.0, _cap_long(_weekly(weekly_km), paces) * 0.4)
        zone = "marathon"
        label = "marathon pace"
    target = pace_target(paces, "goal") or pace_target(paces, zone)
    wu, cd = _wu_cd(paces, 1.5, 1.2)
    steps = [
        wu,
        _step("interval", _dist(work), target, label),
        cd,
    ]
    purpose = f"Race-pace rehearsal: {work:.0f} km at {label}, not an all-out time trial."
    return _finish(f"{work:.0f} km {label}", "tempo", steps, purpose, paces,
                   "goal" if (paces or {}).get("goal") else zone)


def time_trial(paces, race_distance_m=None, **_):
    dist = float(race_distance_m or 5000)
    tt_m = 3000 if dist <= 6000 else 5000
    wu, cd = _wu_cd(paces, 1.6, 1.2)
    steps = [
        wu,
        _repeat(4, [
            _step("interval", {"type": "time", "value": 20, "unit": "s"},
                  pace_target(paces, "rep"), "stride"),
            _step("recovery", _time(1), pace_target(paces, "easy")),
        ]),
        _step("interval", _dist(metres=tt_m), pace_target(paces, "interval"),
              "honest effort"),
        cd,
    ]
    purpose = "A short time trial to check current fitness. Run it honestly; it feeds VDOT and the next block's paces."
    return _finish(f"{tt_m / 1000:g}k time trial", "time_trial", steps, purpose, paces, "interval")


def strides_session(paces, minutes=None, km=None, **_):
    budget = min(_budget_km(minutes, km, paces), 8.0)
    easy_km = max(3.0, budget - 1.0)
    steps = [
        _step("active", _dist(easy_km), pace_target(paces, "easy")),
        _repeat(8, [
            _step("interval", {"type": "time", "value": 20, "unit": "s"},
                  pace_target(paces, "rep"), "stride"),
            _step("recovery", _time(1), pace_target(paces, "easy")),
        ]),
    ]
    purpose = "Keep neuromuscular snap during a taper without loading the legs."
    return _finish("Easy + strides", "easy", steps, purpose, paces, "easy")


def race_day(paces, race_distance_m=None, name="Race", **_):
    km = (float(race_distance_m) / 1000) if race_distance_m else 10.0
    target = pace_target(paces, "goal") or pace_target(paces, "threshold")
    steps = [
        _step("warmup", _dist(1.5), pace_target(paces, "easy"), "shakeout"),
        _step("interval", _dist(km), target, "race"),
    ]
    purpose = "Race day. Trust the taper; start controlled."
    return _finish((name or "Race")[:80], "race", steps, purpose, paces,
                   "goal" if (paces or {}).get("goal") else "threshold")


FACTORIES = {
    "easy": easy,
    "recovery": lambda **kw: easy(recovery=True, **kw),
    "long": long_run,
    "medium_long": medium_long,
    "progression_long": progression_long,
    "mp_long": mp_long,
    "tempo": tempo,
    "cruise": cruise,
    "vo2": vo2,
    "interval": vo2,
    "reps": reps,
    "mix": mix,
    "race_pace": race_pace,
    "time_trial": time_trial,
    "strides": strides_session,
    "race": race_day,
}


def compose(kind: str, paces: dict | None = None, **kwargs) -> dict:
    """Build a validated workout for a planner slot."""
    paces = paces or {}
    kwargs = {**kwargs, "paces": paces}
    if kind == "rest":
        return library.instantiate("rest", paces)
    if kind == "strength":
        return library.instantiate("strength", paces)
    if kind == "bike":
        return library.instantiate("bike-45", paces)
    if kind == "swim":
        return library.instantiate("swim-2000", paces)
    factory = FACTORIES.get(kind) or easy
    return factory(**kwargs)
