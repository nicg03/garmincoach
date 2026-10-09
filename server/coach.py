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
import re
from concurrent.futures import ThreadPoolExecutor
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
diagnose.

Memory:
- Facts the athlete asked you to remember are listed under "Things the \
athlete asked you to remember". Take them into account; the athlete can \
correct them.
- Only if the athlete explicitly asks you to remember something ("remember \
that...", "ricordati che..."), confirm briefly and end your answer with one \
line on its own: `REMEMBER: <the fact, short, in the reply language>`. Never \
write that line otherwise.
- You only know earlier conversations listed under "Earlier conversations". \
If the athlete refers to a past chat you can't see, say so and suggest \
switching on the option to use previous conversations."""

SUMMARY_PROMPT = """Summarize this conversation between an athlete and their \
endurance coach in at most 80 words. Keep what's worth recalling later: \
injuries, goals, preferences, the advice given and any decision taken. No \
preamble, no markdown headings. {lang}"""

BRIEFING_PROMPTS = {
    "en": """Write today's briefing. Three short parts, with a \
markdown heading each:

**Where you are** -- how the last week or two actually went, in load and in \
recovery terms.
**Today** -- what today should look like, concretely (rest, easy, or a \
specific quality session), and why.
**Keep an eye on** -- the one thing most worth watching, or the one thing \
you'd change.

Keep the whole thing under 250 words.""",
    "it": """Scrivi il briefing di oggi. Tre parti brevi, ciascuna con un \
titolo markdown:

**Dove sei** -- come sono andate le ultime una o due settimane, in termini \
di carico e recupero.
**Oggi** -- come dovrebbe essere oggi, in concreto (riposo, facile, o una \
seduta di qualità specifica), e perché.
**Tieni d'occhio** -- la cosa più importante da osservare, o la cosa che \
cambieresti.

Tutto sotto le 250 parole.""",
}

REPLY_LANG = {
    "en": "Always answer in English.",
    "it": "Rispondi sempre in italiano.",
}

MAX_HISTORY_TURNS = 8
RECALL_CHATS = 5
RECALL_DAYS = 90
SUMMARY_MAX_TOKENS = 300
REMEMBER_LINE = re.compile(r"^[ \t>*_`]*REMEMBER:[ \t]*(.+?)[ \t`*_]*$", re.MULTILINE)


def normalize_lang(lang: str | None) -> str:
    return "it" if (lang or "").lower().startswith("it") else "en"


def _system(lang: str) -> str:
    return f"{SYSTEM}\n\n{REPLY_LANG[normalize_lang(lang)]}"


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
    everything = db.full_metrics(user_id)

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
        _pacing_context(user_id, all_activities),
    ])


def _pacing_context(user_id: int, activities: list[dict]) -> str:
    """Paces and today's planned session so the coach doesn't fight the plan."""
    chunks = ["## Training paces (Daniels, min/km)"]
    try:
        perf = db.full_performance(user_id, with_meta=False)
        paces = perf.get("paces") or {}
        slim = {k: paces.get(k) for k in (
            "easy", "easy_range", "marathon", "threshold", "interval", "rep",
            "goal", "source", "note", "vdot") if paces.get(k)}
        chunks.append(json.dumps(slim, default=str))
    except Exception:
        chunks.append("(paces unavailable)")
    try:
        with db.store() as handle:
            plan = handle.active_plan(user_id)
            if not plan:
                plans = handle.list_plans(user_id)
                plan = plans[0] if plans else None
        today = date.today().isoformat()
        if plan:
            session = next((s for s in (plan.get("sessions") or [])
                            if s.get("date") == today), None)
            chunks.append("## Today's planned session\n" + json.dumps({
                "name": (session or {}).get("workout", {}).get("name") if session else None,
                "kind": (session or {}).get("kind"),
                "description": (session or {}).get("description"),
                "purpose": (session or {}).get("purpose"),
                "phase": (session or {}).get("phase"),
                "race": (plan.get("headline") or {}).get("family"),
            }, default=str))
            try:
                from garmin_sync import insights
                body = insights.analyze(plan, activities, None)
                rec = body.get("recommendation")
                slim = [{
                    "date": r.get("date"), "status": r.get("status"),
                    "target": r.get("target"), "actual": r.get("actual"),
                } for r in (body.get("reviews") or [])[-5:]]
                if slim or rec:
                    chunks.append("## Pace Insights (do not change paces unless the athlete accepted)\n"
                                  + json.dumps({
                                      "reviews": slim,
                                      "recommendation": (rec or {}).get("summary"),
                                  }, default=str))
            except Exception:
                pass
    except Exception:
        pass
    return "\n".join(chunks)


def _memory_block(user_id: int) -> str:
    facts = db.memories(user_id)
    if not facts:
        return ""
    return ("## Things the athlete asked you to remember\n"
            + "\n".join(f"- ({m['created'][:10]}) {m['text']}" for m in facts))


def _transcript(messages: list[dict]) -> str:
    names = {"user": "Athlete", "assistant": "Coach"}
    return "\n\n".join(f"{names.get(m['role'], m['role'])}: {m['content'][:1500]}"
                       for m in messages[-20:])


def summarize_chat(user_id: int, chat: dict) -> str | None:
    """A short recap of one conversation, made the first time it's recalled
    and redone only once the chat has grown. Not counted against the daily
    limit: the athlete didn't ask a question."""
    if chat.get("summary") and chat.get("summary_upto", 0) >= chat.get("n_messages", 0):
        return chat["summary"]
    messages = db.chat_messages(user_id, chat["id"])
    if not messages:
        return None
    lang = normalize_lang(chat.get("lang"))
    summary = _ask(SUMMARY_PROMPT.format(lang=REPLY_LANG[lang]),
                   [{"role": "user", "content": _transcript(messages)}],
                   SUMMARY_MAX_TOKENS).strip()
    if summary:
        db.set_chat_summary(user_id, chat["id"], summary, len(messages))
    return summary or None


def earlier_conversations(user_id: int, chat_id: int | None) -> str:
    since = (date.today() - timedelta(days=RECALL_DAYS)).isoformat()
    chats = db.recent_chats(user_id, chat_id, since, RECALL_CHATS)
    if not chats:
        return ""

    def recap(chat):
        try:
            return summarize_chat(user_id, chat)
        except Exception:  # noqa: BLE001 - a missing recap shouldn't sink the answer
            return chat.get("summary")

    with ThreadPoolExecutor(max_workers=len(chats)) as pool:
        recaps = list(pool.map(recap, chats))
    lines = [f"- {chat['updated'][:10]}, \"{chat['title']}\": {text}"
             for chat, text in zip(chats, recaps) if text]
    if not lines:
        return ""
    return "## Earlier conversations (newest first)\n" + "\n".join(lines)


def _messages(user_id: int, question: str, history: list[dict] | None = None,
              extra: str = "") -> list[dict]:
    """Prepend the data to the first user turn so the cache-friendly part of
    the prompt stays put across follow-ups."""
    turns = []
    for turn in (history or [])[-MAX_HISTORY_TURNS:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            turns.append({"role": role, "content": content[:4000]})

    context = "\n\n".join(part for part in (build_context(user_id), extra) if part)
    if turns and turns[0]["role"] == "user":
        turns[0] = {"role": "user",
                    "content": f"Here is my data.\n\n{context}\n\n{turns[0]['content']}"}
        turns.append({"role": "user", "content": question})
        return turns
    return [{"role": "user", "content": f"Here is my data.\n\n{context}\n\n{question}"}]


def _openai_client():
    from openai import OpenAI

    return OpenAI(api_key=config.OPENAI_API_KEY, timeout=config.COACH_TIMEOUT_SECONDS,
                  max_retries=1)


def _anthropic_client():
    import anthropic

    return anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY,
                               timeout=config.COACH_TIMEOUT_SECONDS, max_retries=1)


def _provider() -> str:
    provider = config.coach_provider()
    if provider is None:
        raise RuntimeError("The coach needs OPENAI_API_KEY or ANTHROPIC_API_KEY.")
    return provider


def _ask(system: str, messages: list[dict], max_tokens: int | None = None) -> str:
    max_tokens = max_tokens or config.COACH_MAX_TOKENS
    if _provider() == "openai":
        response = _openai_client().chat.completions.create(
            model=config.coach_model(),
            max_tokens=max_tokens,
            messages=[{"role": "system", "content": system}, *messages],
        )
        return (response.choices[0].message.content or "").strip()
    response = _anthropic_client().messages.create(
        model=config.coach_model(),
        max_tokens=max_tokens,
        system=system,
        messages=messages,
    )
    return "".join(block.text for block in response.content
                   if getattr(block, "type", "") == "text")


def _ask_stream(system: str, messages: list[dict]):
    """The answer as text fragments, whichever provider is configured."""
    if _provider() == "openai":
        response = _openai_client().chat.completions.create(
            model=config.coach_model(),
            max_tokens=config.COACH_MAX_TOKENS,
            messages=[{"role": "system", "content": system}, *messages],
            stream=True,
        )
        for chunk in response:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
        return
    with _anthropic_client().messages.stream(
        model=config.coach_model(),
        max_tokens=config.COACH_MAX_TOKENS,
        system=system,
        messages=messages,
    ) as response:
        yield from response.text_stream


def _complete(user_id: int, question: str,
              history: list[dict] | None = None, lang: str = "en") -> str:
    lang = normalize_lang(lang)
    return _ask(_system(lang),
                _messages(user_id, question, history, extra=_memory_block(user_id)))


def briefing(user_id: int, lang: str = "en") -> str:
    """One-shot daily summary. Not streamed: it gets cached and re-read."""
    lang = normalize_lang(lang)
    return _complete(user_id, BRIEFING_PROMPTS[lang], lang=lang)


def cached_briefing(user_id: int, refresh: bool = False, lang: str = "en") -> dict:
    today = date.today().isoformat()
    lang = normalize_lang(lang)
    if not refresh:
        cached = db.get_briefing(user_id, today, lang)
        if cached:
            return {"date": today, "text": cached, "cached": True, "lang": lang}
    text = briefing(user_id, lang=lang)
    db.save_briefing(user_id, today, text,
                     datetime.now().isoformat(timespec="seconds"), lang)
    return {"date": today, "text": text, "cached": False, "lang": lang}


def split_remember(answer: str) -> tuple[str, list[str]]:
    """The answer without its REMEMBER lines, and the facts they carried."""
    facts = [m.group(1).strip() for m in REMEMBER_LINE.finditer(answer)]
    clean = REMEMBER_LINE.sub("", answer).strip()
    return clean, [f for f in facts if f][:3]


def _event(payload: dict) -> str:
    return "data: " + json.dumps(payload) + "\n\n"


def stream(user_id: int, chat: dict, question: str,
           history: list[dict] | None = None, lang: str = "en"):
    """Server-sent events carrying the answer as it's written.

    The question is already stored; the answer is stored once complete, so a
    failed call leaves the question in the chat but no half-written reply.
    """
    yield _event({"chat": {"id": chat["id"], "title": chat["title"],
                           "use_memory": chat["use_memory"]}})
    try:
        lang = normalize_lang(lang)
        extra = [_memory_block(user_id)]
        if chat.get("use_memory"):
            extra.append(earlier_conversations(user_id, chat["id"]))
        messages = _messages(user_id, question, history,
                             extra="\n\n".join(part for part in extra if part))
        answer = ""
        for delta in _ask_stream(_system(lang), messages):
            answer += delta
            yield _event({"delta": delta})
        clean, facts = split_remember(answer)
        if clean:
            db.add_chat_message(user_id, chat["id"], "assistant", clean)
        for fact in facts:
            try:
                yield _event({"memory": db.add_memory(user_id, fact, chat["id"])})
            except db.MemoryFull:
                yield _event({"memory_full": True})
                break
        yield _event({"done": True, "text": clean})
    except Exception as e:  # noqa: BLE001 - the browser is the only place to report it
        yield _event({"error": f"{type(e).__name__}: {e}"})
