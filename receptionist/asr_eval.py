"""ASR evaluation helpers for the S6 sweep (#28): spoken-form normalisation,
word error rate, and a runaway flag for whisper's decoding loops."""
from __future__ import annotations

import re

_UNITS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
          "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
          "eighteen", "nineteen"]
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty"}
_ORD = {"one": "first", "two": "second", "three": "third", "five": "fifth", "eight": "eighth",
        "nine": "ninth", "twelve": "twelfth", "twenty": "twentieth", "thirty": "thirtieth"}


def _cardinal(n: int) -> str:
    if n < 20:
        return _UNITS[n]
    tens, unit = divmod(n, 10)
    return _TENS[tens * 10] + ("" if unit == 0 else " " + _UNITS[unit])


def _ordinal(n: int) -> str:
    words = _cardinal(n).split()
    last = words[-1]
    words[-1] = _ORD.get(last, last + "th")
    return " ".join(words)


def _num(match: re.Match) -> str:
    n, suffix = int(match.group(1)), match.group(2)
    if n > 59:
        return match.group(0)
    return _ordinal(n) if suffix else _cardinal(n)


def normalise(text: str) -> str:
    t = (text or "").lower()
    t = re.sub(r"\b([ap])\.?\s?m\.?(?=\W|$)", r" \1m", t)          # 2 p.m. / 2pm -> 2 pm
    t = re.sub(r"\b(\d{1,2}):(\d{2})\b", lambda m: f"{m.group(1)} {m.group(2)}" if m.group(2) != "00" else m.group(1), t)
    t = re.sub(r"\b(\d{1,2})(st|nd|rd|th)?\b", _num, t)
    t = re.sub(r"[^a-z' ]+", " ", t)
    return " ".join(t.split())


def wer(reference: str, hypothesis: str) -> float:
    r, h = normalise(reference).split(), normalise(hypothesis).split()
    if not r:
        return 0.0 if not h else 1.0
    d = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        prev, d[0] = d[0], i
        for j, hw in enumerate(h, 1):
            prev, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, prev + (rw != hw))
    return d[len(h)] / len(r)


def is_runaway(hypothesis: str, reference: str, decode_runs: int, max_runs: int = 60) -> bool:
    """A decode loop (far more decoder steps than any short utterance needs) or a
    transcript much longer than the reference (repetition) counts as runaway."""
    h, r = normalise(hypothesis).split(), normalise(reference).split()
    return decode_runs > max_runs or len(h) >= max(4, 1.6 * len(r))
