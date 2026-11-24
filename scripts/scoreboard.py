"""Feels-human checks on review call logs (receptionist JSONL): code only, no AI judge.
Counts the mistakes that made real calls sound robotic. Prints counts, never callers' words.

    python scripts/scoreboard.py call1.jsonl [call2.jsonl ...]
"""
from __future__ import annotations

import json
import re
import sys

FILLER = re.compile(r"^\W*((um+|uh+|hmm+|so|okay|ok|yeah|you know|whoa|like)\W*)+$|^\W*[\[(][^\])]*[\])]\W*$", re.I)
DEAD_END = re.compile(r"outside what I can help with|I'm not sure about that", re.I)
ARE_YOU_THERE = re.compile(r"\b(are you there|hello\?|you still there)\b", re.I)
AVAIL_ASK = re.compile(r"\b(available|availability|openings?|what (times|days)|when are you free)\b", re.I)
OFFERED_TIMES = re.compile(r"\b(I have|is open)\b.*\b(a m|p m)\b", re.I)


def turns_of(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(l) for l in f if l.strip()]
    lat = {r["gen"]: r["end_of_speech_to_audio_s"] for r in rows if r.get("type") == "latency"}
    out = [dict(r, latency=lat.get(r["gen"])) for r in rows if r.get("type") == "turn"]
    return out


def score(turns: list[dict]) -> dict[str, int]:
    s = {"turns": len(turns), "repeated_reply": 0, "answered_filler": 0, "dead_end": 0,
         "caller_asked_are_you_there": 0, "slow_reply_over_3s": 0, "availability_without_times": 0}
    prev = None
    for t in turns:
        heard, reply = t["heard"], t["reply"]
        if prev is not None and reply == prev:
            s["repeated_reply"] += 1
        if FILLER.match(heard):
            s["answered_filler"] += 1           # a person lets "um" pass; it replied to it
        if DEAD_END.search(reply) and not FILLER.match(heard):
            s["dead_end"] += 1
        if ARE_YOU_THERE.search(heard):
            s["caller_asked_are_you_there"] += 1
        if t.get("latency") is not None and t["latency"] > 3.0:
            s["slow_reply_over_3s"] += 1
        if AVAIL_ASK.search(heard) and not OFFERED_TIMES.search(reply):
            s["availability_without_times"] += 1
        prev = reply
    s["robotic_total"] = sum(v for k, v in s.items() if k != "turns")
    return s


def main(argv=None) -> int:
    total: dict[str, int] = {}
    for p in (argv or sys.argv[1:]):
        for k, v in score(turns_of(p)).items():
            total[k] = total.get(k, 0) + v
    print(json.dumps(total, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
