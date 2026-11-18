"""Phone-friendly wording (#38): dates and times as words a TTS voice reads
naturally ("Thursday, December tenth at two p m"), never ISO strings."""
from __future__ import annotations

from datetime import datetime, tzinfo

_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
          "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"]
_TENS = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty", 6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety"}
_ORD = {1: "first", 2: "second", 3: "third", 5: "fifth", 8: "eighth", 9: "ninth", 12: "twelfth"}


def _cardinal(n: int) -> str:
    if n < 20:
        return _UNITS[n]
    t, u = divmod(n, 10)
    return _TENS[t] + ("" if u == 0 else " " + _UNITS[u])


def _ordinal(n: int) -> str:
    if n < 20:
        return _ORD.get(n, _UNITS[n] + "th")
    t, u = divmod(n, 10)
    if u == 0:
        return _TENS[t][:-1] + "ieth"                       # twenty -> twentieth
    return _TENS[t] + " " + _ORD.get(u, _UNITS[u] + "th")


def spoken_time(dt: datetime) -> str:
    if dt.hour == 12 and dt.minute == 0:
        return "noon"
    hour = dt.hour % 12 or 12
    suffix = "p m" if dt.hour >= 12 else "a m"
    if dt.minute == 0:
        return f"{_cardinal(hour)} {suffix}"
    minute = f"oh {_cardinal(dt.minute)}" if dt.minute < 10 else _cardinal(dt.minute)
    return f"{_cardinal(hour)} {minute} {suffix}"


def spoken_when(dt: datetime, tz: tzinfo | None = None) -> str:
    local = dt.astimezone(tz) if tz else dt
    return f"{local:%A}, {local:%B} {_ordinal(local.day)} at {spoken_time(local)}"
