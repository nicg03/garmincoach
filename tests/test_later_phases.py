from datetime import date, timedelta

from garmin_sync import garmin_workout, library, performance, planner, workout_dsl


def test_validate_interval_and_garmin_json():
    w = library.instantiate("int-8x400", {"interval": "4:00"})
    assert w["kind"] == "interval"
    assert w["est_load"] > 0
    body = garmin_workout.to_garmin(w)
    assert body["sportType"]["sportTypeKey"] == "running"
    assert body["workoutSegments"][0]["workoutSteps"]
    kinds = {s.get("type") for s in body["workoutSegments"][0]["workoutSteps"]}
    assert "RepeatGroupDTO" in kinds


def test_strength_and_bike_translate():
    strength = library.instantiate("strength")
    bike = library.instantiate("bike-45")
    swim = library.instantiate("swim-2000")
    assert garmin_workout.to_garmin(strength)["sportType"]["sportTypeKey"] == "strength_training"
    assert garmin_workout.to_garmin(bike)["sportType"]["sportTypeKey"] == "cycling"
    assert garmin_workout.to_garmin(swim)["sportType"]["sportTypeKey"] == "lap_swimming"


def test_vdot_and_riegel():
    # A 20:00 5k is about VDOT 51.
    v = performance.vdot_from(5000, 20 * 60)
    assert v and 48 < v < 54
    recs = [{"mark": "5k", "distance_m": 5000, "time_s": 1200, "time": "20:00",
             "pace": "4:00", "date": "2026-08-01"}]
    preds = performance.predictions(v, recs)
    ten = next(p for p in preds if p["mark"] == "10k")
    assert ten["riegel_s"] > 1200
    assert 41 * 60 < ten["riegel_s"] < 44 * 60


def test_personal_records_from_runs():
    activities = [
        {"id": 1, "type": "running", "start": "2026-07-01 08:00:00",
         "distance_m": 5000, "duration_s": 1260},
        {"id": 2, "type": "running", "start": "2026-08-01 08:00:00",
         "distance_m": 5000, "duration_s": 1200},
        {"id": 3, "type": "cycling", "start": "2026-08-02 08:00:00",
         "distance_m": 40000, "duration_s": 4000},
    ]
    recs = performance.personal_records(activities)
    five = next(r for r in recs if r["mark"] == "5k")
    assert five["time_s"] == 1200
    built = performance.build(activities)
    assert built["vdot"]


def test_planner_builds_weeks_and_guardrails():
    race = {"id": "r1", "name": "City Half", "date": (date.today() + timedelta(days=70)).isoformat(),
            "distance_m": 21097, "goal_time": "1:45:00"}
    activities = [
        {"id": i, "type": "running", "start": f"2026-07-{(i % 28)+1:02d} 07:00:00",
         "distance_m": 8000, "duration_s": 2400, "training_load": 80}
        for i in range(1, 20)
    ]
    plan = planner.generate(race, {}, activities, [], today=date.today().isoformat())
    assert plan["headline"]["weeks"] >= 8
    assert any(s["kind"] == "long" for s in plan["sessions"])
    assert plan["sessions"][-1]["kind"] == "race"
    assert plan["projected"]
    eased = planner.apply_patch(plan, {"op": "ease", "id": plan["sessions"][0]["id"]})
    assert eased["sessions"][0]["kind"] == "easy"


def test_goal_feasibility_and_distribution():
    from garmin_sync.performance import (goal_feasibility, intensity_distribution,
                                         parse_hms, foster)
    assert parse_hms("1:45:00") == 6300
    v = 51
    from garmin_sync.performance import predictions, vdot_from
    recs = [{"mark": "5k", "distance_m": 5000, "time_s": 1200, "time": "20:00",
             "pace": "4:00", "date": "2026-08-01"}]
    preds = predictions(v, recs)
    feas = goal_feasibility(21097, "1:20:00", v, preds)
    assert feas["verdict"] in ("ambitious", "unlikely")
    acts = [{
        "type": "running", "start": "2026-09-10 07:00:00", "duration_s": 3600,
        "hr_zones": [{"zone": 1, "secs": 2400}, {"zone": 4, "secs": 1200}],
        "training_load": 90,
    }]
    dist = intensity_distribution(acts, days=30)
    assert dist["easy_pct"] == 66.7
    fos = foster(acts, days=7)
    assert fos["mean"] is not None


def test_swap_patch_exchanges_dates():
    race = {"id": "r1", "name": "City Half",
            "date": (date.today() + timedelta(days=70)).isoformat(),
            "distance_m": 21097, "goal_time": "1:45:00"}
    plan = planner.generate(race, {}, [], [], today=date.today().isoformat())
    a, b = plan["sessions"][0], plan["sessions"][1]
    da, db = a["date"], b["date"]
    swapped = planner.apply_patch(plan, {"op": "swap", "a": a["id"], "b": b["id"]})
    by_id = {s["id"]: s for s in swapped["sessions"]}
    assert by_id[a["id"]]["date"] == db
    assert by_id[b["id"]]["date"] == da


def test_dsl_rejects_bad_sport():
    try:
        workout_dsl.validate({"name": "x", "sport": "ski", "kind": "easy",
                              "steps": [{"kind": "step", "intensity": "active",
                                         "duration": {"type": "time", "value": 10, "unit": "min"}}]})
    except workout_dsl.WorkoutError:
        return
    raise AssertionError("should have rejected ski")


def _pace_s(text: str) -> int:
    minutes, seconds = text.split(":")
    return int(minutes) * 60 + int(seconds)


def test_vdot_50_daniels_paces():
    p = performance.training_paces(50, None)
    gold = {"threshold": "4:15", "interval": "3:56", "marathon": "4:31", "easy": "5:07"}
    for key, expected in gold.items():
        assert key in p, p
        assert abs(_pace_s(p[key]) - _pace_s(expected)) <= 3, (key, p[key], expected)
    easy = p["bands"]["easy"]
    assert easy["low"] != easy["high"]
    assert _pace_s(easy["low"]) > _pace_s(easy["high"])  # low = slower


def test_garmin_pace_zone_faster_first():
    from garmin_sync import sessions
    paces = performance.training_paces(50, None)
    w = sessions.compose("tempo", paces, weekly_km=45)
    body = garmin_workout.to_garmin(w)
    found = False
    def walk(steps):
        nonlocal found
        for step in steps:
            if step.get("type") == "RepeatGroupDTO":
                walk(step.get("workoutSteps") or [])
                continue
            if (step.get("targetType") or {}).get("workoutTargetTypeKey") == "pace.zone":
                one, two = step.get("targetValueOne"), step.get("targetValueTwo")
                assert one and two and one != two
                assert one > two  # m/s: faster first
                found = True
    walk(body["workoutSegments"][0]["workoutSteps"])
    assert found
    text = workout_dsl.describe(w)
    assert "WU" in text and ("T" in text or "min" in text)


def test_5k_plan_differs_from_marathon():
    today = date.today().isoformat()
    acts = [
        {"id": i, "type": "running", "start": f"2026-07-{(i % 28) + 1:02d} 07:00:00",
         "distance_m": 10000, "duration_s": 3000, "training_load": 90}
        for i in range(1, 24)
    ]
    five = planner.generate(
        {"id": "5", "name": "Park 5k",
         "date": (date.today() + timedelta(days=70)).isoformat(),
         "distance_m": 5000, "goal_time": "20:00"},
        {}, acts, [], today=today)
    mara = planner.generate(
        {"id": "m", "name": "City Marathon",
         "date": (date.today() + timedelta(days=126)).isoformat(),
         "distance_m": 42195, "goal_time": "3:30:00"},
        {}, acts, [], today=today)
    assert five["family"] == "5k"
    assert mara["family"] == "marathon"
    five_blob = " ".join(
        (s.get("description") or "") + " " + (s.get("workout") or {}).get("name", "")
        for s in five["sessions"])
    mara_blob = " ".join(
        (s.get("description") or "") + " " + (s.get("workout") or {}).get("name", "")
        for s in mara["sessions"])
    assert "MP" in mara_blob or "marathon pace" in mara_blob.lower()
    assert "MP long" not in five_blob
    assert any("400" in (s.get("workout") or {}).get("name", "")
               or "I" in (s.get("workout") or {}).get("name", "")
               for s in five["sessions"])


def test_long_cap_and_hard_days_not_adjacent():
    today = date.today()
    plan = planner.generate(
        {"id": "h", "name": "Half",
         "date": (today + timedelta(days=84)).isoformat(),
         "distance_m": 21097, "goal_time": "1:45:00"},
        {}, [], [], today=today.isoformat())
    by_week: dict[str, list] = {}
    for s in plan["sessions"]:
        if s["kind"] == "race":
            continue
        monday = date.fromisoformat(s["date"]) - timedelta(days=date.fromisoformat(s["date"]).weekday())
        by_week.setdefault(monday.isoformat(), []).append(s)
    for week in by_week.values():
        target = next((w["target_km"] for w in plan["weeks"]
                       if w["start"] == week[0]["date"][:10]
                       or True), None)
        # Use the week row whose start is this Monday.
        monday = min(s["date"] for s in week)
        monday = (date.fromisoformat(monday) - timedelta(
            days=date.fromisoformat(monday).weekday())).isoformat()
        row = next((w for w in plan["weeks"] if w["start"] == monday), None)
        week_km = (row or {}).get("target_km") or 40
        longs = [s for s in week if s["kind"] in ("long", "medium_long")]
        for s in longs:
            km = s.get("distance_km") or 0
            assert km <= week_km * 0.30 + 3.5, (km, week_km, s.get("workout", {}).get("name"))
        hard = [date.fromisoformat(s["date"]).weekday()
                for s in week if (s.get("workout") or {}).get("kind") in
                ("tempo", "interval", "time_trial")]
        hard += [date.fromisoformat(s["date"]).weekday()
                 for s in week
                 if any(token in ((s.get("workout") or {}).get("name") or "")
                        for token in ("×", "Tempo", "MP", "cruise", "T "))]
        hard = sorted(set(hard))
        for a, b in zip(hard, hard[1:]):
            assert b - a != 1
