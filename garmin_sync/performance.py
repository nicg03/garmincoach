"""
Performance numbers derived from the same compact activity records the
dashboard already uses.

VDOT and race predictions follow Jack Daniels / Riegel so the planner can
set paces without asking Garmin. Critical speed is the slope of the two
best distance-time points — the same idea as a two-parameter CP model,
without needing a power meter.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from statistics import mean

from .metrics import _num, _r

# Distances we care about, in metres.
MARKS = (
    ("400m", 400),
    ("800m", 800),
    ("1500m", 1500),
    ("Mile", 1609),
    ("3k", 3000),
    ("5k", 5000),
    ("10k", 10000),
    ("15k", 15000),
    ("Half", 21097),
    ("Marathon", 42195),
)

RIEGEL = 1.06


def _day(activity: dict) -> str:
    return (activity.get("start") or "")[:10]


def _is_run(activity: dict) -> bool:
    sport = (activity.get("type") or "").lower()
    return any(token in sport for token in
               ("run", "trail", "track", "treadmill", "virtual_run"))


def _pace_sec_per_km(activity: dict) -> float | None:
    metres = _num(activity.get("distance_m"))
    seconds = _num(activity.get("duration_s"))
    if not metres or not seconds or metres < 200:
        return None
    return seconds / (metres / 1000)


def format_pace(sec_per_km: float | None) -> str | None:
    if sec_per_km is None or sec_per_km <= 0 or sec_per_km > 20 * 60:
        return None
    minutes = int(sec_per_km // 60)
    seconds = int(round(sec_per_km - minutes * 60))
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d}"


def format_hms(seconds: float | None) -> str | None:
    if seconds is None or seconds <= 0:
        return None
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def vo2_at(velocity_m_per_min: float) -> float:
    return -4.60 + 0.182258 * velocity_m_per_min + 0.000104 * velocity_m_per_min ** 2


def pct_vo2max(time_min: float) -> float:
    return (0.8 + 0.1894393 * math.exp(-0.012778 * time_min)
            + 0.2989558 * math.exp(-0.1932605 * time_min))


def vdot_from(distance_m: float, time_s: float) -> float | None:
    if distance_m < 400 or time_s < 50:
        return None
    t_min = time_s / 60
    velocity = distance_m / t_min
    pct = pct_vo2max(t_min)
    if pct <= 0:
        return None
    return _r(vo2_at(velocity) / pct, 1)


def time_from_vdot(distance_m: float, vdot: float) -> float | None:
    """Invert Daniels: binary-search the time that yields this VDOT."""
    if vdot <= 20 or distance_m < 400:
        return None
    lo, hi = 50.0, 8 * 3600.0
    for _ in range(40):
        mid = (lo + hi) / 2
        got = vdot_from(distance_m, mid)
        if got is None:
            return None
        if got > vdot:
            lo = mid
        else:
            hi = mid
    return _r((lo + hi) / 2, 1)


def riegel(time_s: float, from_m: float, to_m: float, exp: float = RIEGEL) -> float:
    return time_s * (to_m / from_m) ** exp


def personal_records(activities: list[dict]) -> list[dict]:
    """Best time at each standard mark, allowing a 5% over-distance."""
    runs = [a for a in activities if _is_run(a) and _num(a.get("distance_m"))
            and _num(a.get("duration_s"))]
    out = []
    for label, mark in MARKS:
        candidates = []
        for a in runs:
            d = a["distance_m"]
            if mark * 0.98 <= d <= mark * 1.08:
                # Scale time back to the exact mark.
                scaled = a["duration_s"] * (mark / d)
                candidates.append((scaled, a))
        if not candidates:
            continue
        scaled, a = min(candidates, key=lambda x: x[0])
        out.append({
            "mark": label,
            "distance_m": mark,
            "time_s": _r(scaled),
            "time": format_hms(scaled),
            "pace": format_pace(scaled / (mark / 1000)),
            "date": _day(a),
            "name": a.get("name"),
            "activity_id": a.get("id"),
        })
    return out


def athlete_vdot(records: list[dict], activities: list[dict]) -> dict:
    """VDOT from the best quality efforts, preferring 5k–half."""
    scored = []
    for rec in records:
        if rec["distance_m"] < 3000 or rec["distance_m"] > 25000:
            continue
        v = vdot_from(rec["distance_m"], rec["time_s"])
        if v:
            scored.append((v, rec))
    if not scored:
        # Fall back to any run longer than 3 km at a plausible race effort.
        for a in activities:
            if not _is_run(a):
                continue
            d, t = _num(a.get("distance_m")), _num(a.get("duration_s"))
            if not d or not t or d < 3000:
                continue
            pace = t / (d / 1000)
            if pace > 7 * 60:
                continue
            v = vdot_from(d, t)
            if v:
                scored.append((v, {"mark": "session", "date": _day(a),
                                   "distance_m": d, "time_s": t}))
    if not scored:
        return {"vdot": None, "from": None}
    vdot, rec = max(scored, key=lambda x: x[0])
    return {"vdot": vdot, "from": rec}


def predictions(vdot: float | None, records: list[dict]) -> list[dict]:
    """Riegel from the nearest PR, plus Daniels-from-VDOT when we have one."""
    out = []
    for label, mark in MARKS:
        if mark < 1500:
            continue
        row = {"mark": label, "distance_m": mark, "vdot_time": None,
               "riegel_time": None, "garmin": None}
        if vdot:
            t = time_from_vdot(mark, vdot)
            row["vdot_time"] = format_hms(t)
            row["vdot_s"] = t
        anchors = [r for r in records if 3000 <= r["distance_m"] <= 25000]
        if anchors:
            # Prefer the closest mark.
            anchor = min(anchors, key=lambda r: abs(r["distance_m"] - mark))
            t = riegel(anchor["time_s"], anchor["distance_m"], mark)
            row["riegel_time"] = format_hms(t)
            row["riegel_s"] = _r(t)
            row["from"] = anchor["mark"]
        out.append(row)
    return out


def critical_speed(records: list[dict]) -> dict | None:
    """CS from the two best mid-distance PRs (e.g. 3k and 5k)."""
    usable = [r for r in records if 800 <= r["distance_m"] <= 15000]
    if len(usable) < 2:
        return None
    usable = sorted(usable, key=lambda r: r["distance_m"])
    a, b = usable[0], usable[-1]
    dt = b["time_s"] - a["time_s"]
    dd = b["distance_m"] - a["distance_m"]
    if dt <= 0 or dd <= 0:
        return None
    cs = dd / dt  # m/s
    return {
        "mps": _r(cs, 3),
        "pace": format_pace(1000 / cs),
        "from": [a["mark"], b["mark"]],
    }


def pace_curve(activities: list[dict]) -> list[dict]:
    """Best pace seen near each mark, for the duration chart."""
    return [
        {"mark": r["mark"], "distance_m": r["distance_m"],
         "pace": r["pace"], "time": r["time"], "date": r["date"]}
        for r in personal_records(activities)
    ]


def decoupling(activity: dict) -> float | None:
    """% drift of second-half pace vs first, from compact splits if present."""
    splits = activity.get("splits") or []
    usable = [s for s in splits if _num(s.get("distance_m")) and _num(s.get("duration_s"))]
    if len(usable) < 4:
        return None
    mid = len(usable) // 2
    def pace(chunk):
        d = sum(s["distance_m"] for s in chunk)
        t = sum(s["duration_s"] for s in chunk)
        return t / (d / 1000) if d else None
    first, second = pace(usable[:mid]), pace(usable[mid:])
    if not first or not second:
        return None
    return _r((second - first) / first * 100, 1)


def efficiency_trend(activities: list[dict], window: int = 8) -> list[dict]:
    """HR at a given easy pace over time — fitness that isn't just volume."""
    points = []
    for a in activities:
        if not _is_run(a):
            continue
        hr = _num(a.get("avg_hr"))
        pace = _pace_sec_per_km(a)
        km = (_num(a.get("distance_m")) or 0) / 1000
        if not hr or not pace or km < 5:
            continue
        # Skip obvious interval days (fast and high HR).
        if pace < 4 * 60:
            continue
        points.append({
            "date": _day(a),
            "hr": hr,
            "pace": format_pace(pace),
            "pace_s": _r(pace),
            "hr_per_pace": _r(hr / (pace / 60), 2),
            "decoupling": decoupling(a),
        })
    return points[-80:]


def velocity_from_vo2(vo2: float) -> float | None:
    """Invert vo2_at: oxygen cost → velocity in metres per minute."""
    if vo2 is None or vo2 <= 0:
        return None
    # vo2 = -4.60 + 0.182258 v + 0.000104 v²
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    disc = b * b - 4 * a * c
    if disc <= 0:
        return None
    return (-b + math.sqrt(disc)) / (2 * a)


def pace_at_vo2_pct(vdot: float, pct: float) -> float | None:
    """Seconds per kilometre at a fraction of VDOT (Daniels %VO2)."""
    v = velocity_from_vo2(vdot * pct)
    if not v or v <= 0:
        return None
    return 60000.0 / v


def _shift_pace_s(sec_per_km: float | None, delta_s: float) -> float | None:
    if sec_per_km is None or sec_per_km <= 0:
        return None
    return max(90.0, sec_per_km + delta_s)


def _band(mid_s: float | None, pad_s: float) -> dict:
    if mid_s is None:
        return {"low": None, "high": None, "mid": None}
    slow = _shift_pace_s(mid_s, pad_s)
    fast = _shift_pace_s(mid_s, -pad_s)
    return {
        "low": format_pace(slow),
        "high": format_pace(fast),
        "mid": format_pace(mid_s),
        "low_s": _r(slow),
        "high_s": _r(fast),
        "mid_s": _r(mid_s),
    }


def _range_band(slow_s: float | None, fast_s: float | None) -> dict:
    if slow_s is None or fast_s is None:
        return {"low": None, "high": None, "mid": None}
    mid = (slow_s + fast_s) / 2
    return {
        "low": format_pace(slow_s),
        "high": format_pace(fast_s),
        "mid": format_pace(mid),
        "low_s": _r(slow_s),
        "high_s": _r(fast_s),
        "mid_s": _r(mid),
    }


def training_paces(vdot: float | None, cs: dict | None,
                   goal_time: str | None = None,
                   goal_distance_m: float | None = None) -> dict:
    """Daniels E/M/T/I/R as min/km ranges the planner and Garmin can use.

    Easy is the official 59–74% VO2 window. M is the VDOT marathon prediction
    when we have one. T/I/R sit at 88 / 98 / 105% of VDOT, each as a narrow
    band so the watch has a real pace.zone instead of a single number.
    """
    out = {
        "source": None,
        "note": None,
        "vdot": vdot,
        "bands": {},
    }
    mid: dict[str, float] = {}

    if vdot:
        easy_slow = pace_at_vo2_pct(vdot, 0.59)
        easy_fast = pace_at_vo2_pct(vdot, 0.74)
        easy_mid = pace_at_vo2_pct(vdot, 0.70)
        t_s = pace_at_vo2_pct(vdot, 0.88)
        i_s = pace_at_vo2_pct(vdot, 0.98)
        r_s = pace_at_vo2_pct(vdot, 1.05)
        m_s = None
        t_mar = time_from_vdot(42195, vdot)
        if t_mar:
            m_s = t_mar / 42.195
        else:
            m_s = pace_at_vo2_pct(vdot, 0.80)
        if easy_slow and easy_fast:
            out["bands"]["easy"] = _range_band(easy_slow, easy_fast)
            out["bands"]["easy"]["mid"] = format_pace(easy_mid)
            out["bands"]["easy"]["mid_s"] = _r(easy_mid) if easy_mid else None
        if easy_mid:
            mid["easy"] = easy_mid
            mid["long"] = easy_mid
        rec_s = _shift_pace_s(easy_slow, 18) if easy_slow else None
        if rec_s and easy_slow:
            out["bands"]["recovery"] = _range_band(rec_s, easy_slow)
            mid["recovery"] = (rec_s + easy_slow) / 2
        if m_s:
            out["bands"]["marathon"] = _band(m_s, 4)
            mid["marathon"] = m_s
        if t_s:
            out["bands"]["threshold"] = _band(t_s, 4)
            mid["threshold"] = t_s
        if i_s:
            out["bands"]["interval"] = _band(i_s, 3)
            mid["interval"] = i_s
        if r_s:
            out["bands"]["rep"] = _band(r_s, 3)
            mid["rep"] = r_s
        if mid:
            out["source"] = "vdot"

    if cs and cs.get("mps") and not mid.get("threshold"):
        t_s = 1000 / cs["mps"]
        out["bands"]["threshold"] = _band(t_s, 4)
        mid.setdefault("threshold", t_s)
        mid.setdefault("easy", t_s * 1.20)
        mid.setdefault("long", t_s * 1.18)
        mid.setdefault("marathon", t_s * 1.08)
        mid.setdefault("interval", t_s * 0.93)
        mid.setdefault("rep", t_s * 0.87)
        out["bands"].setdefault("easy", _band(mid["easy"], 12))
        out["source"] = out["source"] or "critical_speed"

    for key in ("easy", "long", "marathon", "threshold", "interval", "rep", "recovery"):
        if key in mid:
            out[key] = format_pace(mid[key])
        band = out["bands"].get(key) or {}
        if band.get("low") and band.get("high"):
            out[f"{key}_range"] = f"{band['high']}–{band['low']}"

    goal_s = parse_hms(goal_time)
    dist = _num(goal_distance_m)
    if goal_s and dist and dist >= 1000:
        gp = goal_s / (dist / 1000)
        out["bands"]["goal"] = _band(gp, 5)
        out["goal"] = format_pace(gp)
        out["goal_range"] = f"{out['bands']['goal']['high']}–{out['bands']['goal']['low']}"

    if not mid:
        out["note"] = "Need a recent 5k-ish effort or time trial to set training paces."
    return out


def pace_target(paces: dict | None, zone: str) -> dict | None:
    """DSL pace target from a training_paces() payload."""
    paces = paces or {}
    band = (paces.get("bands") or {}).get(zone) or {}
    low, high = band.get("low"), band.get("high")
    if low and high:
        return {"type": "pace", "low": low, "high": high}
    mid = paces.get(zone)
    if mid:
        return {"type": "pace", "low": mid, "high": mid}
    return None


def parse_hms(text: str | None) -> float | None:
    if not text or not isinstance(text, str):
        return None
    parts = text.strip().replace(".", ":").split(":")
    try:
        nums = [int(p) for p in parts if p != ""]
    except ValueError:
        return None
    if not nums:
        return None
    if len(nums) == 1:
        return float(nums[0])
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    return nums[0] * 3600 + nums[1] * 60 + nums[2]


def intensity_distribution(activities: list[dict], days: int = 42) -> dict:
    """Share of running time in easy (Z1–2) vs hard (Z3–5). Polarised ~80/20."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()
    easy = hard = unknown = 0.0
    for a in activities:
        if not _is_run(a) or (_day(a) or "") < cutoff:
            continue
        zones = a.get("hr_zones") or []
        placed = False
        if isinstance(zones, list) and zones:
            for z in zones:
                secs = _num((z or {}).get("secs")) or 0
                zone = int(_num((z or {}).get("zone")) or 0)
                if zone <= 2:
                    easy += secs
                elif zone >= 3:
                    hard += secs
                placed = True
        if not placed:
            dur = _num(a.get("duration_s")) or 0
            unknown += dur
    total = easy + hard
    if total <= 0:
        return {"easy_pct": None, "hard_pct": None, "easy_h": 0, "hard_h": 0,
                "unknown_h": _r(unknown / 3600, 1), "window_days": days}
    return {
        "easy_pct": _r(100 * easy / total, 1),
        "hard_pct": _r(100 * hard / total, 1),
        "easy_h": _r(easy / 3600, 1),
        "hard_h": _r(hard / 3600, 1),
        "unknown_h": _r(unknown / 3600, 1),
        "window_days": days,
    }


def foster(activities: list[dict], days: int = 7) -> dict:
    """Foster monotony and strain on the last `days` of daily load."""
    from .metrics import load_by_day
    by = load_by_day(activities)
    end = date.today()
    loads = [by.get((end - timedelta(days=i)).isoformat(), 0.0) for i in range(days)]
    loads = list(reversed(loads))
    if not any(loads):
        return {"monotony": None, "strain": None, "mean": 0, "days": days}
    avg = mean(loads)
    var = mean((x - avg) ** 2 for x in loads)
    std = math.sqrt(var) if var > 0 else 0.0
    monotony = avg / std if std else None
    strain = (sum(loads) * monotony) if monotony else None
    return {
        "monotony": _r(monotony, 2) if monotony else None,
        "strain": _r(strain) if strain else None,
        "mean": _r(avg),
        "days": days,
    }


def goal_feasibility(distance_m: float | None, goal_time: str | None,
                     vdot: float | None, predictions: list[dict] | None) -> dict:
    """How a race goal sits against Daniels/Riegel from current fitness."""
    goal_s = parse_hms(goal_time)
    dist = _num(distance_m)
    pred = None
    if dist and predictions:
        pred = min(predictions, key=lambda p: abs((p.get("distance_m") or 0) - dist))
        if abs((pred.get("distance_m") or 0) - dist) > max(800, dist * 0.08):
            pred = None
    expected_s = (pred or {}).get("vdot_s") or (pred or {}).get("riegel_s")
    if not goal_s or not expected_s:
        return {"verdict": "unknown", "goal_s": goal_s, "expected_s": expected_s,
                "delta_s": None, "label": "Need a goal time and a recent 5k-ish effort."}
    delta = goal_s - expected_s
    pct = delta / expected_s
    if pct > 0.03:
        verdict, label = "comfortable", "Inside current fitness, with a margin."
    elif pct > -0.03:
        verdict, label = "on_pace", "Matches current VDOT. Honest goal."
    elif pct > -0.08:
        verdict, label = "ambitious", "A stretch. Needs a clean build and a good day."
    else:
        verdict, label = "unlikely", "Faster than current fitness by a wide margin."
    return {
        "verdict": verdict,
        "label": label,
        "goal_s": goal_s,
        "expected_s": _r(expected_s),
        "delta_s": _r(delta),
        "expected": format_hms(expected_s),
        "goal": format_hms(goal_s),
    }


def reconcile_zones(meta: dict | None, vdot: float | None) -> dict:
    """Watch HR zones next to paces implied by VDOT."""
    garmin = (meta or {}).get("hr_zones")
    if isinstance(garmin, dict) and "value" in garmin:
        garmin = garmin["value"]
    paces = training_paces(vdot, None)
    return {"garmin": garmin, "paces": paces}


def race_splits(distance_m: float | None, goal_s: float | None) -> list[dict]:
    """Even splits with a slight negative-split suggestion."""
    dist = _num(distance_m)
    if not dist or not goal_s or dist < 1000:
        return []
    km = dist / 1000
    even = goal_s / km
    return [
        {"kind": "even", "pace": format_pace(even), "label": "Even"},
        {"kind": "negative", "pace": format_pace(even * 1.01),
         "second": format_pace(even * 0.99),
         "label": "Negative (first half +1%, second −1%)"},
    ]


def build(activities: list[dict], meta: dict | None = None) -> dict:
    records = personal_records(activities)
    vdot = athlete_vdot(records, activities)
    cs = critical_speed(records)
    garmin_pred = None
    raw = (meta or {}).get("race_predictions") or {}
    if isinstance(raw, dict):
        garmin_pred = (raw.get("value") if "value" in raw else raw)
    preds = predictions(vdot.get("vdot"), records)
    if isinstance(garmin_pred, dict):
        mapping = {"time5K": "5k", "time10K": "10k", "timeHalfMarathon": "Half",
                   "timeMarathon": "Marathon", "5K": "5k", "10K": "10k"}
        by_mark = {p["mark"]: p for p in preds}
        for key, mark in mapping.items():
            secs = garmin_pred.get(key) or garmin_pred.get(key.lower())
            if isinstance(secs, (int, float)) and mark in by_mark:
                by_mark[mark]["garmin"] = format_hms(secs)
                by_mark[mark]["garmin_s"] = secs
    return {
        "vdot": vdot.get("vdot"),
        "vdot_from": vdot.get("from"),
        "critical_speed": cs,
        "records": records,
        "predictions": preds,
        "paces": training_paces(vdot.get("vdot"), cs),
        "curve": pace_curve(activities),
        "efficiency": efficiency_trend(activities),
        "distribution": intensity_distribution(activities),
        "foster": foster(activities),
        "zones": reconcile_zones(meta, vdot.get("vdot")),
    }
