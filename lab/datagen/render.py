"""Render dates/times/durations as caller phrases. Pure functions; the label never depends on these."""
from __future__ import annotations

from datetime import date

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
NUM = ["twelve", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]

# style -> required day offset from the reference date (None = any)
RELATIVE_OFFSETS = {"tomorrow": (1, 1), "day_after_tomorrow": (2, 2), "in_n_days": (3, 10), "weekday": (1, 6)}
EXPLICIT_DATE_STYLES = ("iso", "month_day", "day_month")


def ordinal(n: int) -> str:
    suffix = "th" if 11 <= n % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def date_phrase(target: date, ref: date, style: str) -> str:
    delta = (target - ref).days
    if style == "iso":
        return target.isoformat()
    if style == "month_day":
        return f"{MONTHS[target.month - 1]} {ordinal(target.day)}"
    if style == "day_month":
        return f"the {ordinal(target.day)} of {MONTHS[target.month - 1]}"
    if style == "tomorrow":
        assert delta == 1
        return "tomorrow"
    if style == "day_after_tomorrow":
        assert delta == 2
        return "the day after tomorrow"
    if style == "in_n_days":
        assert delta >= 2
        return f"in {delta} days"
    if style == "weekday":
        assert 1 <= delta <= 6
        return f"this {WEEKDAYS[target.weekday()]}"
    raise ValueError(style)


def _hour12(h: int) -> int:
    return h % 12 or 12


def _period(h: int, m: int) -> str:
    if h < 12:
        return "in the morning"
    if h < 17:
        return "in the afternoon"
    return "in the evening"


def time_phrase(hm: str, style: str) -> str:
    h, m = (int(x) for x in hm.split(":"))
    ampm = "am" if h < 12 else "pm"
    if style == "h12":
        assert m == 0
        return f"{_hour12(h)}{ampm}"
    if style == "h12min":
        return f"{_hour12(h)}:{m:02d} {ampm}"
    if style == "h24":
        return f"{h:02d}:{m:02d}"
    if style == "words":
        if (h, m) == (12, 0):
            return "noon"
        if m == 0:
            return f"{NUM[_hour12(h)]} {_period(h, m)}"
        if m == 15:
            return f"quarter past {NUM[_hour12(h)]} {_period(h, m)}"
        if m == 30:
            return f"half past {NUM[_hour12(h)]} {_period(h, m)}"
        if m == 45:
            nh = (h + 1) % 24
            return f"quarter to {NUM[_hour12(nh)]} {_period(h, m)}"
        raise ValueError(hm)
    raise ValueError(style)


def duration_phrase(minutes: int, style: str) -> str:
    if style == "minutes":
        return f"for {minutes} minutes"
    if style == "words":
        return {15: "for a quarter of an hour", 30: "for half an hour", 60: "for an hour"}[minutes]
    raise ValueError(style)
