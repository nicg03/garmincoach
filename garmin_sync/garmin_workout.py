"""
Translate the workout DSL into the JSON Garmin's workout-service accepts.

The numeric ids below are the ones Garmin's own web app sends. They have
been stable across the 2024–2026 client; if a create ever 400s, this is
the file to reconcile against a workout you made by hand and re-read.
"""
from __future__ import annotations

from . import endpoints as ep
from .workout_dsl import pace_to_mps, validate

SPORT = {
    "running": {"sportTypeId": 1, "sportTypeKey": "running", "displayOrder": 1},
    "cycling": {"sportTypeId": 2, "sportTypeKey": "cycling", "displayOrder": 2},
    "swimming": {"sportTypeId": 4, "sportTypeKey": "lap_swimming", "displayOrder": 5},
    "strength": {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 9},
}

STEP_TYPE = {
    "warmup": {"stepTypeId": 1, "stepTypeKey": "warmup"},
    "cooldown": {"stepTypeId": 2, "stepTypeKey": "cooldown"},
    "interval": {"stepTypeId": 3, "stepTypeKey": "interval"},
    "recovery": {"stepTypeId": 4, "stepTypeKey": "recovery"},
    "rest": {"stepTypeId": 5, "stepTypeKey": "rest"},
    "active": {"stepTypeId": 3, "stepTypeKey": "interval"},
}

END = {
    "lap": {"conditionTypeId": 1, "conditionTypeKey": "lap.button", "displayOrder": 1,
            "displayable": True},
    "time": {"conditionTypeId": 2, "conditionTypeKey": "time", "displayOrder": 2,
             "displayable": True},
    "distance": {"conditionTypeId": 3, "conditionTypeKey": "distance", "displayOrder": 3,
                 "displayable": True},
    "reps": {"conditionTypeId": 7, "conditionTypeKey": "reps", "displayOrder": 7,
             "displayable": True},
}

TARGET = {
    "none": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
    "hr_zone": {"workoutTargetTypeId": 4, "workoutTargetTypeKey": "heart.rate.zone"},
    "pace": {"workoutTargetTypeId": 6, "workoutTargetTypeKey": "pace.zone"},
    "power_zone": {"workoutTargetTypeId": 2, "workoutTargetTypeKey": "power.zone"},
    "rpe": {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target"},
}

# Garmin stores repeat groups as RepeatGroup with a nested list of steps.
REPEAT_TYPE = {"stepTypeId": 6, "stepTypeKey": "repeat"}


def _seconds(duration: dict) -> float:
    value = float(duration.get("value") or 0)
    unit = duration.get("unit") or "s"
    kind = duration.get("type")
    if kind == "time":
        if unit in ("min", "m", "minutes"):
            return value * 60
        if unit in ("h", "hr", "hours"):
            return value * 3600
        return value
    if kind == "distance":
        return value * 1000 if unit == "km" else value
    if kind == "reps":
        return value
    return 1


def _step(step: dict, order: list[int]) -> dict:
    order[0] += 1
    if step.get("kind") == "repeat":
        children = [_step(s, order) for s in step.get("steps") or []]
        return {
            "type": "RepeatGroupDTO",
            "stepType": REPEAT_TYPE,
            "stepOrder": order[0],
            "childStepId": 1,
            "numberOfIterations": int(step.get("times") or 1),
            "workoutSteps": children,
            "skipLastRestStep": False,
            "smartRepeat": False,
        }

    duration = step.get("duration") or {"type": "lap", "value": 1}
    kind = duration.get("type")
    target = step.get("target") or {"type": "none"}
    target_kind = target.get("type") or "none"
    node = {
        "type": "ExecutableStepDTO",
        "stepType": STEP_TYPE.get(step.get("intensity") or "active", STEP_TYPE["interval"]),
        "stepOrder": order[0],
        "intensity": (step.get("intensity") or "active").upper(),
        "endCondition": END.get(kind, END["lap"]),
        "endConditionValue": _seconds(duration),
        "targetType": TARGET.get(target_kind, TARGET["none"]),
    }
    if target_kind == "hr_zone":
        node["zoneNumber"] = int(target["zone"])
        node["targetValueOne"] = float(target["zone"])
        node["targetValueTwo"] = float(target["zone"])
    elif target_kind == "power_zone":
        node["zoneNumber"] = int(target["zone"])
        node["targetValueOne"] = float(target["zone"])
        node["targetValueTwo"] = float(target["zone"])
    elif target_kind == "pace":
        speeds = [s for s in (
            pace_to_mps(target.get("low")),
            pace_to_mps(target.get("high")),
        ) if s]
        if len(speeds) == 1:
            # A single number is not a zone Garmin can track. Widen ±3 s/km.
            mid = speeds[0]
            pace_s = 1000.0 / mid
            speeds = [1000.0 / max(1.0, pace_s - 3), 1000.0 / (pace_s + 3)]
        if len(speeds) >= 2:
            # targetValueOne = faster (higher m/s), Two = slower.
            node["targetValueOne"] = round(max(speeds), 6)
            node["targetValueTwo"] = round(min(speeds), 6)
    if step.get("exercise"):
        node["category"] = "GENERIC"
        node["exerciseName"] = step["exercise"]
        node["weightValue"] = step.get("weight_kg") or 0
        node["weightDisplayUnit"] = {"unitKey": "kilogram"}
    if step.get("note"):
        node["description"] = step["note"]
    return node


def to_garmin(workout: dict) -> dict:
    """Garmin workout-service body, ready to POST."""
    clean = validate(workout)
    sport = SPORT[clean["sport"]]
    order = [0]
    steps = [_step(s, order) for s in clean["steps"]]
    return {
        "workoutName": clean["name"],
        "description": clean.get("description") or "",
        "sportType": sport,
        "workoutProvider": "Garmin Coach",
        "workoutSourceId": None,
        "isSessionTransitionEnabled": False,
        "estimatedDurationInSecs": clean.get("est_seconds") or 0,
        "estimatedDistanceInMeters": 0,
        "workoutSegments": [{
            "segmentOrder": 1,
            "sportType": sport,
            "workoutSteps": steps,
        }],
    }


def create_op(workout: dict) -> dict:
    return {
        "op": "create_workout",
        "method": "POST",
        "path": ep.WORKOUT_CREATE,
        "body": to_garmin(workout),
    }


def update_op(workout_id: int, workout: dict) -> dict:
    body = to_garmin(workout)
    body["workoutId"] = int(workout_id)
    return {
        "op": "update_workout",
        "method": "PUT",
        "path": ep.WORKOUT.format(workout_id=int(workout_id)),
        "body": body,
    }


def schedule_op(workout_id: int, day: str) -> dict:
    return {
        "op": "schedule",
        "method": "POST",
        "path": ep.WORKOUT_SCHEDULE.format(workout_id=int(workout_id)),
        "body": {"date": day},
    }


def unschedule_op(schedule_id: int) -> dict:
    return {
        "op": "unschedule",
        "method": "DELETE",
        "path": ep.WORKOUT_UNSCHEDULE.format(schedule_id=int(schedule_id)),
        "body": None,
    }
