"""
A small built-in library of running (and a few other) sessions.

The planner instantiates these with the athlete's own paces. Users can add
their own templates on top; these are just so a first plan is never empty.
"""
from __future__ import annotations

from copy import deepcopy

from .performance import pace_target
from .workout_dsl import validate


def _tgt(paces, zone, hr_zone=2):
    target = pace_target(paces, zone)
    if target:
        return target
    value = (paces or {}).get(zone)
    if value:
        return {"type": "pace", "low": value, "high": value}
    return {"type": "hr_zone", "zone": hr_zone}


def _easy(name, minutes, paces=None):
    return {
        "name": name, "sport": "running", "kind": "easy",
        "steps": [
            {"kind": "step", "intensity": "active",
             "duration": {"type": "time", "value": minutes, "unit": "min"},
             "target": _tgt(paces, "easy")},
        ],
    }


def _long(name, minutes, paces=None):
    w = _easy(name, minutes, paces)
    w["kind"] = "long"
    return w


def _tempo(name, warmup, work, cooldown, paces=None):
    return {
        "name": name, "sport": "running", "kind": "tempo",
        "steps": [
            {"kind": "step", "intensity": "warmup",
             "duration": {"type": "time", "value": warmup, "unit": "min"},
             "target": _tgt(paces, "easy")},
            {"kind": "step", "intensity": "interval",
             "duration": {"type": "time", "value": work, "unit": "min"},
             "target": _tgt(paces, "threshold", 4)},
            {"kind": "step", "intensity": "cooldown",
             "duration": {"type": "time", "value": cooldown, "unit": "min"},
             "target": _tgt(paces, "easy")},
        ],
    }


def _reps(name, warmup, reps, dist_m, rest_s, cooldown, paces=None, zone="interval"):
    return {
        "name": name, "sport": "running", "kind": "interval",
        "steps": [
            {"kind": "step", "intensity": "warmup",
             "duration": {"type": "time", "value": warmup, "unit": "min"},
             "target": _tgt(paces, "easy")},
            {"kind": "repeat", "times": reps, "steps": [
                {"kind": "step", "intensity": "interval",
                 "duration": {"type": "distance", "value": dist_m, "unit": "m"},
                 "target": _tgt(paces, zone, 5)},
                {"kind": "step", "intensity": "recovery",
                 "duration": {"type": "time", "value": rest_s, "unit": "s"},
                 "target": _tgt(paces, "recovery")},
            ]},
            {"kind": "step", "intensity": "cooldown",
             "duration": {"type": "time", "value": cooldown, "unit": "min"},
             "target": _tgt(paces, "easy")},
        ],
    }


def _strength(name):
    return {
        "name": name, "sport": "strength", "kind": "strength",
        "steps": [
            {"kind": "repeat", "times": 3, "steps": [
                {"kind": "step", "intensity": "interval",
                 "duration": {"type": "reps", "value": 8},
                 "exercise": "SQUAT"},
                {"kind": "step", "intensity": "interval",
                 "duration": {"type": "reps", "value": 8},
                 "exercise": "PUSH_UP"},
                {"kind": "step", "intensity": "interval",
                 "duration": {"type": "reps", "value": 8},
                 "exercise": "BENT_OVER_ROW"},
                {"kind": "step", "intensity": "recovery",
                 "duration": {"type": "time", "value": 60, "unit": "s"}},
            ]},
        ],
    }


def _bike(name, minutes):
    return {
        "name": name, "sport": "cycling", "kind": "bike",
        "steps": [
            {"kind": "step", "intensity": "active",
             "duration": {"type": "time", "value": minutes, "unit": "min"},
             "target": {"type": "power_zone", "zone": 2}},
        ],
    }


def _swim(name, metres):
    return {
        "name": name, "sport": "swimming", "kind": "swim",
        "steps": [
            {"kind": "step", "intensity": "warmup",
             "duration": {"type": "distance", "value": 200, "unit": "m"}},
            {"kind": "repeat", "times": max(1, metres // 100 - 2), "steps": [
                {"kind": "step", "intensity": "interval",
                 "duration": {"type": "distance", "value": 100, "unit": "m"}},
            ]},
            {"kind": "step", "intensity": "cooldown",
             "duration": {"type": "distance", "value": 100, "unit": "m"}},
        ],
    }


BUILTINS = [
    ("easy-30", lambda p: _easy("Easy 30", 30, p)),
    ("easy-45", lambda p: _easy("Easy 45", 45, p)),
    ("easy-60", lambda p: _easy("Easy 60", 60, p)),
    ("long-75", lambda p: _long("Long 75", 75, p)),
    ("long-90", lambda p: _long("Long 90", 90, p)),
    ("long-120", lambda p: _long("Long 2h", 120, p)),
    ("tempo-20", lambda p: _tempo("Tempo 20", 15, 20, 10, p)),
    ("tempo-30", lambda p: _tempo("Tempo 30", 15, 30, 10, p)),
    ("int-8x400", lambda p: _reps("8×400", 15, 8, 400, 75, 10, p, "interval")),
    ("int-5x1000", lambda p: _reps("5×1000", 15, 5, 1000, 120, 10, p, "interval")),
    ("int-6x800", lambda p: _reps("6×800", 15, 6, 800, 90, 10, p, "interval")),
    ("strides", lambda p: _reps("Strides", 20, 6, 100, 60, 10, p, "rep")),
    ("strength", lambda p: _strength("Strength A")),
    ("bike-45", lambda p: _bike("Easy spin", 45)),
    ("swim-2000", lambda p: _swim("2000 swim", 2000)),
    ("rest", lambda p: {"name": "Rest", "sport": "running", "kind": "rest", "steps": []}),
]


def instantiate(key: str, paces: dict | None = None) -> dict:
    paces = paces or {}
    for name, factory in BUILTINS:
        if name == key:
            return validate(factory(paces))
    raise KeyError(key)


def catalogue(paces: dict | None = None) -> list[dict]:
    out = []
    for key, factory in BUILTINS:
        try:
            w = validate(factory(paces or {}))
        except Exception:
            w = factory(paces or {})
        item = deepcopy(w)
        item["key"] = key
        out.append(item)
    return out
