"""
A small, sport-agnostic language for structured workouts.

The site, the planner and the coach all speak this. Garmin's own JSON is
built from it in `garmin_workout.py`, so a change to how we describe an
interval never has to know about `stepTypeId`.

A workout is a name, a sport, and a list of steps. A step is either a single
effort or a repeat of other steps. Durations are time, distance, reps or
"until the lap button". Targets are optional: HR zone, pace, power zone, or
none (RPE / open).
"""
from __future__ import annotations

from copy import deepcopy

SPORTS = ("running", "cycling", "swimming", "strength")
INTENSITIES = ("warmup", "cooldown", "interval", "recovery", "rest", "active")
DURATION_TYPES = ("time", "distance", "reps", "lap")
TARGET_TYPES = ("none", "hr_zone", "pace", "power_zone", "rpe")
KINDS = ("easy", "long", "tempo", "interval", "recovery", "rest",
         "race", "strength", "swim", "bike", "medium_long", "time_trial")

# Rough load per minute at each kind. Used to project CTL; Garmin's own load
# replaces it after the session is completed.
LOAD_PER_MIN = {
    "easy": 0.8, "recovery": 0.5, "long": 1.1, "medium_long": 1.05,
    "tempo": 1.6, "interval": 2.0, "time_trial": 2.1, "race": 2.2,
    "strength": 0.9, "swim": 0.9, "bike": 0.9, "rest": 0.0,
}


class WorkoutError(ValueError):
    pass


def _num(value, name: str, lo=None, hi=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkoutError(f"{name} must be a number")
    if lo is not None and value < lo:
        raise WorkoutError(f"{name} is too small")
    if hi is not None and value > hi:
        raise WorkoutError(f"{name} is too large")
    return value


def _duration_seconds(duration: dict, step: dict | None = None) -> float:
    kind = duration.get("type")
    value = _num(duration.get("value"), "duration.value", 0)
    unit = duration.get("unit") or ("s" if kind == "time" else "m")
    if kind == "time":
        if unit in ("min", "m", "minutes"):
            return value * 60
        if unit in ("h", "hr", "hours"):
            return value * 3600
        return value
    if kind == "distance":
        metres = value * 1000 if unit in ("km",) else value
        pace_s = 330.0
        target = (step or {}).get("target") or {}
        if target.get("type") == "pace":
            secs = [pace_to_seconds(target.get("low")),
                    pace_to_seconds(target.get("high"))]
            secs = [s for s in secs if s]
            if secs:
                pace_s = sum(secs) / len(secs)
        return metres / 1000 * pace_s
    if kind == "reps":
        return value * 8
    if kind == "lap":
        return 60
    raise WorkoutError(f"unknown duration type {kind!r}")


def _validate_duration(duration: dict) -> dict:
    if not isinstance(duration, dict):
        raise WorkoutError("each step needs a duration")
    kind = duration.get("type")
    if kind not in DURATION_TYPES:
        raise WorkoutError(f"duration type must be one of {DURATION_TYPES}")
    _num(duration.get("value"), "duration.value", 0, 200_000)
    return duration


def _validate_target(target: dict | None) -> dict | None:
    if not target:
        return None
    if not isinstance(target, dict):
        raise WorkoutError("target must be an object")
    kind = target.get("type") or "none"
    if kind not in TARGET_TYPES:
        raise WorkoutError(f"target type must be one of {TARGET_TYPES}")
    if kind == "hr_zone":
        _num(target.get("zone"), "target.zone", 1, 5)
    if kind == "power_zone":
        _num(target.get("zone"), "target.zone", 1, 7)
    if kind == "pace":
        if not target.get("low") and not target.get("high"):
            raise WorkoutError("pace target needs low and/or high")
    if kind == "rpe":
        _num(target.get("value"), "target.value", 1, 10)
    return target


def _validate_step(step: dict, depth: int = 0) -> dict:
    if not isinstance(step, dict):
        raise WorkoutError("step must be an object")
    if depth > 3:
        raise WorkoutError("repeats nested too deep")
    kind = step.get("kind") or "step"
    if kind == "repeat":
        times = int(_num(step.get("times"), "repeat.times", 1, 40))
        inner = step.get("steps") or []
        if not isinstance(inner, list) or not inner:
            raise WorkoutError("a repeat needs steps")
        return {"kind": "repeat", "times": times,
                "steps": [_validate_step(s, depth + 1) for s in inner]}
    intensity = step.get("intensity") or "active"
    if intensity not in INTENSITIES:
        raise WorkoutError(f"intensity must be one of {INTENSITIES}")
    out = {
        "kind": "step",
        "intensity": intensity,
        "duration": _validate_duration(step.get("duration") or {"type": "lap", "value": 1}),
        "target": _validate_target(step.get("target")),
    }
    if step.get("exercise"):
        out["exercise"] = str(step["exercise"])[:80]
    if step.get("weight_kg") is not None:
        out["weight_kg"] = _num(step["weight_kg"], "weight_kg", 0, 500)
    if step.get("note"):
        out["note"] = str(step["note"])[:200]
    return out


def validate(workout: dict) -> dict:
    """Return a cleaned copy, or raise WorkoutError."""
    if not isinstance(workout, dict):
        raise WorkoutError("workout must be an object")
    name = (workout.get("name") or "").strip()
    if not name or len(name) > 80:
        raise WorkoutError("give the workout a name (1–80 characters)")
    sport = workout.get("sport") or "running"
    if sport not in SPORTS:
        raise WorkoutError(f"sport must be one of {SPORTS}")
    session = workout.get("kind") or "easy"
    if session not in KINDS:
        raise WorkoutError(f"kind must be one of {KINDS}")
    steps = workout.get("steps") or []
    if session != "rest" and (not isinstance(steps, list) or not steps):
        raise WorkoutError("a workout needs at least one step")
    cleaned = {
        "name": name,
        "sport": sport,
        "kind": session,
        "steps": [_validate_step(s) for s in steps] if session != "rest" else [],
    }
    if workout.get("description"):
        cleaned["description"] = str(workout["description"])[:800]
    if workout.get("purpose"):
        cleaned["purpose"] = str(workout["purpose"])[:280]
    if isinstance(workout.get("targets"), dict):
        cleaned["targets"] = {
            str(k)[:40]: str(v)[:80]
            for k, v in list(workout["targets"].items())[:12]
        }
    if workout.get("distance_km") is not None:
        try:
            cleaned["distance_km"] = round(float(workout["distance_km"]), 2)
        except (TypeError, ValueError):
            pass
    cleaned["est_load"] = estimate_load(cleaned)
    cleaned["est_seconds"] = estimate_seconds(cleaned)
    return cleaned


def _walk(steps: list[dict], times: int = 1):
    for step in steps:
        if step.get("kind") == "repeat":
            yield from _walk(step.get("steps") or [], times * int(step.get("times") or 1))
        else:
            yield times, step


def estimate_seconds(workout: dict) -> int:
    total = 0.0
    for times, step in _walk(workout.get("steps") or []):
        total += times * _duration_seconds(
            step.get("duration") or {"type": "lap", "value": 1}, step)
    return int(round(total))


def estimate_load(workout: dict) -> float:
    minutes = estimate_seconds(workout) / 60
    rate = LOAD_PER_MIN.get(workout.get("kind") or "easy", 0.9)
    return round(max(0.0, minutes * rate), 1)


def describe(workout: dict) -> str:
    """Human summary: WU → set → CD, with paces when present."""
    if (workout.get("kind") or "") == "rest":
        return "Rest"
    if workout.get("description"):
        return workout["description"]
    parts = [_step_label(s) for s in (workout.get("steps") or [])]
    if parts:
        return " → ".join(parts)
    secs = workout.get("est_seconds") or estimate_seconds(workout)
    mins = max(1, round(secs / 60))
    return f"{workout.get('name') or workout.get('kind')} · {mins} min"


def _fmt_duration(duration: dict | None) -> str:
    duration = duration or {}
    kind = duration.get("type")
    value = duration.get("value") or 0
    unit = duration.get("unit") or ""
    if kind == "distance":
        if unit == "km" or (unit != "m" and value >= 100 and value == int(value) and value % 1000 == 0):
            km = value if unit == "km" else value / 1000
            return f"{km:g} km" if km >= 1 else f"{int(km * 1000)} m"
        metres = value * 1000 if unit == "km" else value
        if metres >= 1000 and metres % 100 == 0:
            return f"{metres / 1000:g} km"
        return f"{int(metres)} m"
    if kind == "time":
        if unit in ("min", "m", "minutes") or (unit in ("s", "sec") and value >= 60 and value % 60 == 0):
            mins = value if unit in ("min", "m", "minutes") else value / 60
            return f"{mins:g} min"
        if unit in ("s", "sec", "") or unit == "s":
            if value >= 60:
                return f"{int(value)} s" if value % 60 else f"{int(value // 60)} min"
            return f"{int(value)} s"
        return f"{value:g} {unit or 's'}"
    if kind == "reps":
        return f"{int(value)} reps"
    if kind == "lap":
        return "lap"
    return str(value)


def _pace_label(target: dict | None) -> str:
    target = target or {}
    if target.get("type") != "pace":
        return ""
    low, high = target.get("low"), target.get("high")
    if low and high and low != high:
        return f" @ {high}–{low}/km"
    if low or high:
        return f" @ {low or high}/km"
    return ""


def _step_label(step: dict) -> str:
    if step.get("kind") == "repeat":
        inner = " + ".join(_step_label(s) for s in step.get("steps") or [])
        return f"{step.get('times') or 1}×({inner})"
    intensity = step.get("intensity") or "active"
    prefix = {"warmup": "WU", "cooldown": "CD", "recovery": "rec",
              "rest": "rest"}.get(intensity, "")
    body = _fmt_duration(step.get("duration")) + _pace_label(step.get("target"))
    if prefix:
        return f"{prefix} {body}"
    return body


def pace_to_seconds(pace) -> float | None:
    """'4:30' min/km → seconds per kilometre."""
    if isinstance(pace, (int, float)) and not isinstance(pace, bool):
        return float(pace) if 60 < pace < 20 * 60 else None
    if not isinstance(pace, str) or ":" not in pace:
        return None
    try:
        minutes, seconds = pace.strip().split(":", 1)
        total = int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return None
    return total if total > 0 else None


def pace_to_mps(pace) -> float | None:
    """'4:30' min/km, or a number already in m/s, to metres per second."""
    if isinstance(pace, (int, float)) and not isinstance(pace, bool):
        return float(pace) if pace > 0 else None
    if not isinstance(pace, str) or ":" not in pace:
        return None
    try:
        minutes, seconds = pace.strip().split(":", 1)
        total = int(minutes) * 60 + float(seconds)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    return 1000 / total


def clone(workout: dict) -> dict:
    return deepcopy(workout)
