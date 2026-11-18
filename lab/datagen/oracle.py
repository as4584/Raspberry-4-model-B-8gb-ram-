"""Deterministic reference policy: semantic frame + reference time -> exact expected tool call.

Labels are computed from the sampled frame, never parsed back out of generated text.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

TIMEZONE = "America/New_York"
OPEN, CLOSE = time(9, 0), time(17, 0)
CLOSED_WEEKDAYS = {6}  # Sunday
DEFAULT_DURATION = 30
CLARIFY_QUESTION = {
    "date": "Which day would you like?",
    "time": "What time would you like, between 9am and 5pm Monday to Saturday?",
    "reference": "Could you give me your booking reference?",
}
HANDOFF = {"faq_unknown": "unknown_question", "human": "caller_request",
           "emergency": "emergency", "injection": "unsafe_request"}
SLOT_INTENTS = ("book", "availability", "reschedule")


def local_time_status(d: date, hm: str, tz: ZoneInfo) -> str:
    naive = datetime.combine(d, time.fromisoformat(hm))
    a, b = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    if a.utcoffset() == b.utcoffset():
        return "ok"
    roundtrip = a.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
    return "nonexistent" if roundtrip != naive else "ambiguous"


def clarify(missing: str) -> dict:
    return {"name": "ask_clarification", "arguments": {"missing": missing, "question": CLARIFY_QUESTION[missing]}}


def _slot_problem(f: dict, now: datetime, need_time: bool) -> str | None:
    # Always the business zone: a fixed-offset timestamp would hide DST gaps/folds.
    tz = ZoneInfo(TIMEZONE)
    now = now.astimezone(tz)
    if f["date_status"] != "ok" or f["date"] is None:
        return "date"
    d: date = f["date"]
    if f["time_status"] == "ok" and f["time"] and local_time_status(d, f["time"], tz) != "ok":
        return "time"
    if d < now.date() or d.weekday() in CLOSED_WEEKDAYS:
        return "date"
    if f["time_status"] != "ok" or f["time"] is None:
        return "time" if (need_time or f["time_status"] == "ambiguous") else None
    start = time.fromisoformat(f["time"])
    end_dt = datetime.combine(d, start) + timedelta(minutes=f["duration"])
    if start < OPEN or end_dt.date() != d or end_dt.time() > CLOSE:
        return "time"
    if datetime.combine(d, start, tzinfo=tz) <= now:
        return "time"
    return None


def label(f: dict, now: datetime) -> dict:
    intent = f["intent"]
    if intent in HANDOFF:
        return {"name": "handoff_to_human", "arguments": {"reason": HANDOFF[intent]}}
    if intent == "faq":
        return {"name": "answer_faq", "arguments": {"topic": f["topic"]}}
    if intent in ("cancel", "reschedule") and not f.get("reference"):
        return clarify("reference")
    if intent == "cancel":
        return {"name": "cancel_appointment", "arguments": {"reference": f["reference"]}}
    problem = _slot_problem(f, now, need_time=intent != "availability")
    if problem:
        return clarify(problem)
    d, dur = f["date"].isoformat(), f["duration"]
    if intent == "availability":
        t = f["time"] if f["time_status"] == "ok" else None
        return {"name": "check_availability", "arguments": {"date": d, "time": t, "duration_min": dur}}
    if intent == "book":
        return {"name": "book_appointment", "arguments": {"date": d, "time": f["time"], "duration_min": dur}}
    if intent == "reschedule":
        return {"name": "reschedule_appointment",
                "arguments": {"reference": f["reference"], "date": d, "time": f["time"], "duration_min": dur}}
    raise ValueError(intent)
