"""Deterministic spoken date/time resolution (S3). A transcript or a model
supplies the phrase; this code decides the instant. Anything ambiguous or
impossible returns None so the dialogue asks instead of guessing:
no time, an hour without am/pm (unless 13-23 h), no day, past times,
invalid dates, and DST-nonexistent/ambiguous local times."""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july",
                                        "august", "september", "october", "november", "december"], 1)}
WEEKDAYS = {d: i for i, d in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"])}
_UNITS = {w: i for i, w in enumerate(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                                       "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
                                       "seventeen", "eighteen", "nineteen"])}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
        "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
        "fifteenth": 15, "sixteenth": 16, "seventeenth": 17, "eighteenth": 18, "nineteenth": 19,
        "twentieth": 20, "thirtieth": 30}


def _words_to_digits(text: str) -> str:
    """'twenty eighth' -> '28', 'nine thirty' -> '9 30', '10th' -> '10'."""
    out, toks, i = [], text.split(), 0
    while i < len(toks):
        t = toks[i]
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t in _TENS and (nxt in _UNITS and 0 < _UNITS[nxt] < 10 or nxt in _ORD and _ORD[nxt] < 10):
            out.append(str(_TENS[t] + _UNITS.get(nxt, _ORD.get(nxt, 0))))
            i += 2
            continue
        if t in _TENS:
            out.append(str(_TENS[t]))
        elif t in _UNITS:
            out.append(str(_UNITS[t]))
        elif t in _ORD:
            out.append(str(_ORD[t]))
        else:
            out.append(re.sub(r"^(\d{1,2})(st|nd|rd|th)$", r"\1", t))
        i += 1
    return " ".join(out)


def _normalise(phrase: str) -> str:
    t = phrase.lower()
    t = re.sub(r"\b([ap])\.\s?m\.?", r"\1m", t)               # p.m. -> pm
    t = re.sub(r"(\d)([ap]m)\b", r"\1 \2", t)                  # 2pm -> 2 pm
    t = re.sub(r"[^a-z0-9: ]+", " ", t)
    return _words_to_digits(" ".join(t.split()))


def _time(t: str) -> time | None:
    if re.search(r"\b(noon|midday)\b", t):
        return time(12, 0)
    m = re.search(r"\b(\d{1,2})(?::(\d{2})|\s(\d{2}))?\s*(am|pm)?\b(?!\s*\d)", t.split(" at ", 1)[-1] if " at " in t else t)
    if not m:
        return None
    hour, minute, ampm = int(m.group(1)), int(m.group(2) or m.group(3) or 0), m.group(4)
    if minute > 59:
        return None
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    elif not 13 <= hour <= 23:
        return None                                            # "at two" / "3:15": am or pm?
    return time(hour, minute)


def _date(t: str, today: date) -> tuple[date | None, bool]:
    """(date, roll_year): roll_year means 'no year given, move to next year if past'."""
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1), False
    if re.search(r"\btoday\b", t):
        return today, False
    for name, idx in WEEKDAYS.items():
        if re.search(rf"\b{name}\b", t):
            ahead = (idx - today.weekday()) % 7 or 7
            return today + timedelta(days=ahead), False
    month = next((MONTHS[w] for w in t.split() if w in MONTHS), None)
    if month is None:
        return None, False
    name = next(w for w in t.split() if w in MONTHS)
    year_m = re.search(r"\b(20\d\d)\b", t)
    before = re.search(rf"\b(\d{{1,2}})\s+of\s+{name}\b", t)
    after = re.search(rf"\b{name}\s+(\d{{1,2}})\b(?!:)", t)
    day = int((before or after).group(1)) if (before or after) else None
    if day is None:
        return None, False
    try:
        d = date(int(year_m.group(1)) if year_m else today.year, month, day)
    except ValueError:
        return None, False
    return d, not year_m


def resolve(phrase: str, now: datetime) -> datetime | None:
    if not phrase or not phrase.strip():
        return None
    t = _normalise(phrase)
    tz = now.tzinfo
    d, roll = _date(t, now.astimezone(tz).date())
    clock = _time(t)
    if d is None or clock is None:
        return None
    naive = datetime.combine(d, clock)
    if roll and naive.replace(tzinfo=tz) <= now:
        try:
            naive = naive.replace(year=naive.year + 1)
        except ValueError:
            return None
    a, b = naive.replace(tzinfo=tz, fold=0), naive.replace(tzinfo=tz, fold=1)
    if a.utcoffset() != b.utcoffset():
        return None                                             # DST fold: ambiguous
    if a.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None) != naive:
        return None                                             # DST gap: nonexistent
    return a if a > now else None
