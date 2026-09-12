"""
The coach: your history, compressed into a prompt, handed to GPT (or Claude).

The whole trick is the context. Sending raw records would burn tokens on
things a coach doesn't need (activity IDs, per-day step counts from a year
ago), so this builds three small tables instead:

  - the last ~90 days, one line per day, with load, fatigue, fitness, form and
    the recovery markers next to their baselines
  - every week of the entire history, one line each, so long-term trends stay
    visible without long-term detail
  - recent sessions, so "what did I actually do" has an answer

They go in as CSV because it's the densest readable format there is: a year of
history costs a few thousand tokens instead of tens of thousands.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from garmin_sync import metrics

from . import config, db

SYSTEM = """You are a level-headed endurance coach reading one athlete's own \
Garmin data. You get their training load history and their recovery markers, \
and you answer like a coach who has seen the numbers, not like a dashboard \
reading itself out loud.

How to think:
- Training load is exponentially smoothed: ATL is 7-day fatigue, CTL is \
42-day fitness, form is CTL minus ATL. The load ratio is ATL/CTL; roughly \
0.8-1.3 is a productive range, above 1.5 is where trouble starts.
- Recovery markers only mean something against that person's own baseline, \
which is why each one comes with its trailing average. HRV below baseline \
alongside resting HR above baseline, for two or three days running, is the \
signal that matters. A single odd night is noise.
- Missing values are missing, not zero. Say so instead of guessing.

How to answer:
- Lead with the answer, then the evidence. Cite the actual numbers.
- Be specific and short. A few sentences or a handful of bullets.
- Markdown for structure, but no tables and no preamble.
- If the data doesn't support a conclusion, say what you'd need.
- You are not a doctor. Point them at one for anything medical, and don't \
diagnose."""

BRIEFING_PROMPT = """Write today's briefing. Three short parts, with a \
markdown heading each:

**Where you are** -- how the last week or two actually went, in load and in \
recovery terms.
**Today** -- what today should look like, concretely (rest, easy, or a \
specific quality session), and why.
**Keep an eye on** -- the one thing most worth watching, or the one thing \
you'd change.

Keep the whole thing under 250 words."""

MAX_HISTORY_TURNS = 8


def _csv(header: str, rows: list[list]) -> str:
    lines = [header]
    for row in rows:
        lines.append(",".join("" if v is None else str(v) for v in row))
    return "\n".join(lines)


def _daily_table(built: dict) -> str:
    by_date = {row["date"]: row for row in built["wellness"]}
    rows = []
    for t in built["training"]:
        w = by_date.get(t["date"], {})
        rows.append([
            t["date"], t["load"], t["atl"], t["ctl"], t["form"],
            w.get("hrv"), w.get("hrv_base"), w.get("resting_hr"),
            w.get("resting_hr_base"), w.get("sleep_score"), w.get("sleep_h"),
            w.get("readiness"), w.get("body_battery_low"), w.get("steps"),
        ])
    return _csv("date,load,atl,ctl,form,hrv,hrv_baseline,resting_hr,"
                "resting_hr_baseline,sleep_score,sleep_hours,readiness,"
                "body_battery_low,steps", rows)


def _weekly_table(weeks: list[dict]) -> str:
    rows = [[w["start"], w["sessions"], w["hours"], w["km"], w["load"],
             " ".join(f"{sport}:{vals['sessions']}"
                      for sport, vals in sorted(w["by_type"].items()))]
            for w in weeks]
    return _csv("week_starting,sessions,hours,km,load,sports", rows)


def _round(value, digits=1):
    """Garmin hands back full float precision; nobody needs 14 decimals of
    training effect, least of all a language model paying by the token."""
    return round(value, digits) if isinstance(value, (int, float)) else None


def _activity_table(activities: list[dict]) -> str:
    rows = []
    for a in activities:
        minutes = _round(a["duration_s"] / 60) if a.get("duration_s") else None
        km = _round(a["distance_m"] / 1000, 2) if a.get("distance_m") else None
        rows.append([a.get("start"), a.get("type"), a.get("name"), minutes, km,
                     a.get("avg_hr"), a.get("max_hr"), _round(a.get("training_load")),
                     _round(a.get("aerobic_te")), _round(a.get("anaerobic_te"))])
    return _csv("start,sport,name,minutes,km,avg_hr,max_hr,load,aerobic_te,"
                "anaerobic_te", rows)


def build_context(user_id: int) -> str:
    """Everything the model gets to look at, as one text block.

    Scoped to one account: the coach only ever sees the history of whoever is
    asking.
    """
    today = date.today()
    detail_start = (today - timedelta(days=config.COACH_DETAIL_DAYS)).isoformat()

    info = db.status(user_id)
    all_days, all_activities = db.window(user_id, None, None)
    built = metrics.build(all_days, all_activities, visible_from=detail_start)
    everything = metrics.build(all_days, all_activities)

    recent_cut = (today - timedelta(days=28)).isoformat()
    recent = [a for a in all_activities if (a.get("start") or "")[:10] >= recent_cut]

    return "\n\n".join([
        f"Today is {today.isoformat()}.",
        f"History held: {info['days']} days and {info['activities']} activities, "
        f"{info['first']} to {info['last']}."
        + (f" Note: the most recent data is from {info['last']}, so the last "
           "few days may simply not be synced yet."
           if info["last"] and info["last"] != today.isoformat() else ""),
        "## Daily detail (last "
        f"{config.COACH_DETAIL_DAYS} days)\n" + _daily_table(built),
        "## Every week on record\n" + _weekly_table(everything["weeks"]),
        "## Sessions in the last 4 weeks\n" + _activity_table(recent),
        "## Where things stand right now\n"
        + json.dumps(everything["headline"], default=str),
    ])


def _messages(user_id: int, question: str,
              history: list[dict] | None = None) -> list[dict]:
    """Prepend the data to the first user turn so the cache-friendly part of
    the prompt stays put across follow-ups."""
    turns = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            turns.append({"role": role, "content": content[:4000]})

    context = build_context(user_id)
    if turns and turns[0]["role"] == "user":
        turns[0] = {"role": "user",
                    "content": f"Here is my data.\n\n{context}\n\n{turns[0]['content']}"}
        turns.append({"role": "user", "content": question})
        return turns
    return [{"role": "user", "content": f"Here is my data.\n\n{context}\n\n{question}"}]


def _openai_client():
    from openai import OpenAI

    return OpenAI(api_key=config.OPENAI_API_KEY)


def _anthropic_client():
    import anthropic

    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


def _complete(user_id: int, question: str,
              history: list[dict] | None = None) -> str:
    provider = config.coach_provider()
    if provider is None:
        raise RuntimeError("The coach needs OPENAI_API_KEY or ANTHROPIC_API_KEY.")
    messages = _messages(user_id, question, history)
    if provider == "openai":
        response = _openai_client().chat.completions.create(
            model=config.coach_model(),
            max_tokens=config.COACH_MAX_TOKENS,
            messages=[{"role": "system", "content": SYSTEM}, *messages],
        )
        return (response.choices[0].message.content or "").strip()
    response = _anthropic_client().messages.create(
        model=config.coach_model(),
        max_tokens=config.COACH_MAX_TOKENS,
        system=SYSTEM,
        messages=messages,
    )
    return "".join(block.text for block in response.content
                   if getattr(block, "type", "") == "text")


def briefing(user_id: int) -> str:
    """One-shot daily summary. Not streamed: it gets cached and re-read."""
    return _complete(user_id, BRIEFING_PROMPT)


def cached_briefing(user_id: int, refresh: bool = False) -> dict:
    today = date.today().isoformat()
    if not refresh:
        cached = db.get_briefing(user_id, today)
        if cached:
            return {"date": today, "text": cached, "cached": True}
    text = briefing(user_id)
    db.save_briefing(user_id, today, text,
                     datetime.now().isoformat(timespec="seconds"))
    return {"date": today, "text": text, "cached": False}


def stream(user_id: int, question: str, history: list[dict] | None = None):
    """Server-sent events carrying the answer as it's written."""
    try:
        provider = config.coach_provider()
        if provider is None:
            raise RuntimeError("The coach needs OPENAI_API_KEY or ANTHROPIC_API_KEY.")
        messages = _messages(user_id, question, history)
        if provider == "openai":
            response = _openai_client().chat.completions.create(
                model=config.coach_model(),
                max_tokens=config.COACH_MAX_TOKENS,
                messages=[{"role": "system", "content": SYSTEM}, *messages],
                stream=True,
            )
            for chunk in response:
                delta = chunk.choices[0].delta.content if chunk.choices else None
                if delta:
                    yield "data: " + json.dumps({"delta": delta}) + "\n\n"
        else:
            with _anthropic_client().messages.stream(
                model=config.coach_model(),
                max_tokens=config.COACH_MAX_TOKENS,
                system=SYSTEM,
                messages=messages,
            ) as response:
                for chunk in response.text_stream:
                    yield "data: " + json.dumps({"delta": chunk}) + "\n\n"
        yield "data: " + json.dumps({"done": True}) + "\n\n"
    except Exception as e:  # noqa: BLE001 - the browser is the only place to report it
        yield "data: " + json.dumps({"error": f"{type(e).__name__}: {e}"}) + "\n\n"
