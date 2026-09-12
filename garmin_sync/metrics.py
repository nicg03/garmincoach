"""
Derived metrics -- the numbers the dashboard and the coach reason about.

Pure functions over the compact records that `summarize.py` produces, with no
I/O and no third-party imports, so the local app and the deployed server can
both import this module. `build()` is the single entry point: hand it the day
and activity records for a window and it returns everything the charts need.

Two ideas do most of the work:

  - Training load is exponentially smoothed twice. ATL (7-day) is the fatigue
    you feel now, CTL (42-day) is the fitness you've built, and form is
    CTL - ATL: negative means you're digging a hole, positive means you're
    fresh (or detraining).
  - Recovery numbers are meaningless in absolute terms -- an HRV of 45 is good
    for one person and alarming for another. Every wellness series is therefore
    paired with its own trailing baseline and reported as a deviation from it.
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

ATL_SPAN = 7
CTL_SPAN = 42
BASELINE_WINDOW = 7


def _num(x):
    """The value if it's a usable number, else None (bools are not numbers)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return x


def _r(x, n=1):
    return round(x, n) if isinstance(x, (int, float)) else x


def date_span(start: str, end: str) -> list[str]:
    """Every ISO date from `start` to `end` inclusive, ascending."""
    d0, d1 = date.fromisoformat(start), date.fromisoformat(end)
    if d1 < d0:
        d0, d1 = d1, d0
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _day_of(activity: dict) -> str:
    return (activity.get("start") or "")[:10]


# ---- training load ----------------------------------------------------------
def load_by_day(activities: list[dict]) -> dict[str, float]:
    """Total training load per calendar day.

    Garmin only reports `training_load` for activities recorded on a device
    that computes it; for the rest we approximate with a duration-weighted
    aerobic training effect so easy walks don't silently vanish from the chart.
    """
    out: dict[str, float] = {}
    for a in activities:
        day = _day_of(a)
        if not day:
            continue
        load = _num(a.get("training_load"))
        if load is None:
            te = _num(a.get("aerobic_te")) or 0
            mins = (_num(a.get("duration_s")) or 0) / 60
            load = te * mins * 0.5
        out[day] = out.get(day, 0.0) + max(0.0, float(load))
    return out


def ema(values: list[float], span: int) -> list[float]:
    """Exponential moving average, seeded with the first value."""
    if not values:
        return []
    alpha = 2 / (span + 1)
    out = [float(values[0])]
    for v in values[1:]:
        out.append(alpha * float(v) + (1 - alpha) * out[-1])
    return out


def training_series(activities: list[dict], dates: list[str]) -> list[dict]:
    """Per-day load with ATL, CTL and form.

    Rest days count as zero load -- that's the whole point of the smoothing.
    """
    by_day = load_by_day(activities)
    loads = [by_day.get(d, 0.0) for d in dates]
    atl, ctl = ema(loads, ATL_SPAN), ema(loads, CTL_SPAN)
    return [
        {"date": d, "load": _r(loads[i]), "atl": _r(atl[i]),
         "ctl": _r(ctl[i]), "form": _r(ctl[i] - atl[i])}
        for i, d in enumerate(dates)
    ]


def acwr(series: list[dict]) -> float | None:
    """Acute-to-chronic workload ratio from the last point. ~0.8-1.3 is the
    commonly cited sweet spot; well above it is where injuries cluster."""
    if not series:
        return None
    last = series[-1]
    ctl = last.get("ctl") or 0
    if ctl <= 0:
        return None
    return _r((last.get("atl") or 0) / ctl, 2)


# ---- wellness ---------------------------------------------------------------
def rolling_mean(values: list[float | None], window: int) -> list[float | None]:
    """Trailing mean over the last `window` entries, ignoring gaps."""
    out: list[float | None] = []
    for i in range(len(values)):
        chunk = [v for v in values[max(0, i - window + 1):i + 1] if v is not None]
        out.append(_r(mean(chunk)) if chunk else None)
    return out


def _day_index(days: list[dict]) -> dict[str, dict]:
    return {d["date"]: d for d in days if d.get("date")}


def _extract(day: dict | None) -> dict:
    """Flatten one day record into the handful of fields we chart."""
    day = day or {}
    daily = day.get("daily") or {}
    sleep = day.get("sleep") or {}
    # Garmin returns an all-zero sleep record for nights it has nothing for.
    # Nobody sleeps zero hours, so treat that as missing rather than plotting
    # a night of no sleep.
    if not _num(sleep.get("total_h")):
        sleep = {}
    return {
        "hrv": _num(day.get("hrv_avg")),
        "hrv_status": day.get("hrv_status"),
        "readiness": _num(day.get("training_readiness")),
        "resting_hr": _num(daily.get("resting_hr") or sleep.get("resting_hr")),
        "steps": _num(daily.get("steps")),
        "stress": _num(daily.get("avg_stress")),
        "body_battery_high": _num(daily.get("body_battery_high")),
        "body_battery_low": _num(daily.get("body_battery_low")),
        "intensity_min": _num(daily.get("intensity_min")),
        "sleep_score": _num(sleep.get("score")),
        "sleep_h": _num(sleep.get("total_h")),
        "deep_h": _num(sleep.get("deep_h")),
        "light_h": _num(sleep.get("light_h")),
        "rem_h": _num(sleep.get("rem_h")),
        "awake_h": _num(sleep.get("awake_h")),
    }


RECOVERY_FIELDS = ("hrv", "resting_hr", "sleep_score", "readiness", "body_battery_high")


def wellness_series(days: list[dict], dates: list[str]) -> list[dict]:
    """One row per date with the wellness fields plus a baseline and a
    deviation for each recovery metric."""
    idx = _day_index(days)
    rows = [{"date": d, **_extract(idx.get(d))} for d in dates]
    for field in RECOVERY_FIELDS:
        base = rolling_mean([r[field] for r in rows], BASELINE_WINDOW)
        for i, r in enumerate(rows):
            r[f"{field}_base"] = base[i]
            r[f"{field}_delta"] = (
                _r(r[field] - base[i]) if r[field] is not None and base[i] else None
            )
    return rows


# ---- volume -----------------------------------------------------------------
def weekly_volume(activities: list[dict]) -> list[dict]:
    """Sessions, hours and kilometres per ISO week, broken down by sport."""
    weeks: dict[str, dict] = {}
    for a in activities:
        day = _day_of(a)
        if not day:
            continue
        d = date.fromisoformat(day)
        y, w, _ = d.isocalendar()
        key = f"{y}-W{w:02d}"
        week = weeks.setdefault(key, {"week": key, "start": None, "sessions": 0,
                                      "hours": 0.0, "km": 0.0, "load": 0.0,
                                      "by_type": {}})
        week["start"] = (d - timedelta(days=d.weekday())).isoformat()
        hours = (_num(a.get("duration_s")) or 0) / 3600
        km = (_num(a.get("distance_m")) or 0) / 1000
        week["sessions"] += 1
        week["hours"] += hours
        week["km"] += km
        sport = week["by_type"].setdefault(
            a.get("type") or "other", {"sessions": 0, "hours": 0.0, "km": 0.0})
        sport["sessions"] += 1
        sport["hours"] += hours
        sport["km"] += km
    by_day_load = load_by_day(activities)
    for day, load in by_day_load.items():
        y, w, _ = date.fromisoformat(day).isocalendar()
        if f"{y}-W{w:02d}" in weeks:
            weeks[f"{y}-W{w:02d}"]["load"] += load

    out = []
    for week in sorted(weeks.values(), key=lambda w: w["week"]):
        week["hours"] = _r(week["hours"], 2)
        week["km"] = _r(week["km"], 2)
        week["load"] = _r(week["load"])
        for sport in week["by_type"].values():
            sport["hours"] = _r(sport["hours"], 2)
            sport["km"] = _r(sport["km"], 2)
        out.append(week)
    return out


def load_vs_next_hrv(activities: list[dict], days: list[dict],
                     dates: list[str]) -> list[dict]:
    """Yesterday's load against this morning's HRV -- the pairing that answers
    'is my body absorbing this?'."""
    by_day = load_by_day(activities)
    idx = _day_index(days)
    points = []
    for d in dates:
        nxt = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
        hrv = _num((idx.get(nxt) or {}).get("hrv_avg"))
        if hrv is None:
            continue
        points.append({"date": d, "load": _r(by_day.get(d, 0.0)), "next_hrv": hrv})
    return points


# ---- headline ---------------------------------------------------------------
def headline(training: list[dict], wellness: list[dict],
             weeks: list[dict]) -> dict:
    """The few numbers worth putting in big type at the top of the page."""
    last_t = training[-1] if training else {}
    last_w = next((r for r in reversed(wellness) if r.get("hrv") is not None
                   or r.get("sleep_score") is not None), {})
    this_week = weeks[-1] if weeks else {}
    prev_week = weeks[-2] if len(weeks) > 1 else {}
    return {
        "atl": last_t.get("atl"),
        "ctl": last_t.get("ctl"),
        "form": last_t.get("form"),
        "acwr": acwr(training),
        "hrv": last_w.get("hrv"),
        "hrv_delta": last_w.get("hrv_delta"),
        "hrv_status": last_w.get("hrv_status"),
        "resting_hr": last_w.get("resting_hr"),
        "resting_hr_delta": last_w.get("resting_hr_delta"),
        "sleep_score": last_w.get("sleep_score"),
        "sleep_h": last_w.get("sleep_h"),
        "readiness": last_w.get("readiness"),
        "week_km": this_week.get("km"),
        "week_hours": this_week.get("hours"),
        "week_load": this_week.get("load"),
        "week_sessions": this_week.get("sessions"),
        "prev_week_load": prev_week.get("load"),
    }


def build(days: list[dict], activities: list[dict],
          start: str | None = None, end: str | None = None,
          visible_from: str | None = None) -> dict:
    """Everything the dashboard needs, in one payload.

    `days` and `activities` are the stored summaries; `start`/`end` bound the
    computation and default to the span the data itself covers.

    `visible_from` trims the returned series without changing the maths. Pass
    it whatever the user asked to see and let `start` reach further back: a
    42-day CTL and a 7-day baseline are both wrong on their first points, so
    they need history before the window to be worth plotting.
    """
    known = [d["date"] for d in days if d.get("date")]
    known += [_day_of(a) for a in activities if _day_of(a)]
    if not known and not (start and end):
        return {"dates": [], "training": [], "wellness": [], "weeks": [],
                "load_vs_hrv": [], "headline": {}}
    start = start or min(known)
    end = end or max(max(known), date.today().isoformat())
    dates = date_span(start, end)

    training = training_series(activities, dates)
    wellness = wellness_series(days, dates)
    weeks = weekly_volume(activities)
    cut = visible_from or start
    # A week counts as visible if any of it falls inside the window, otherwise
    # the partial week the window starts in would silently disappear.
    def week_visible(week: dict) -> bool:
        monday = week.get("start")
        if not monday:
            return False
        return (date.fromisoformat(monday) + timedelta(days=6)).isoformat() >= cut

    return {
        "dates": [d for d in dates if d >= cut],
        "training": [r for r in training if r["date"] >= cut],
        "wellness": [r for r in wellness if r["date"] >= cut],
        "weeks": [w for w in weeks if week_visible(w)],
        "load_vs_hrv": [p for p in load_vs_next_hrv(activities, days, dates)
                        if p["date"] >= cut],
        "headline": headline(training, wellness, weeks),
    }
