"""Local AI judge (Ollama, desktop GPU): rates receptionist conversations Good / Okay / Bad with a
fixed checklist. It only earns a vote after it agrees with the owner's own ratings (>= 80%).
Desktop only; stdlib only.

    python lab/judge.py --items judge_items.json --out judge_ratings.json [--model qwen3.6:27b]
    python lab/judge.py --agreement judge_ratings.json owner_ratings.json
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request

RUBRIC = """You are grading a phone call between a caller (C) and an AI receptionist (R) for LexMakesIt,
a small tech business. The caller's words are speech-to-text and may be misheard. Rate how a real caller
would feel about the receptionist, using this checklist:
- Did R understand what the caller actually wanted (allowing for mishearing)?
- Did R move the call forward (answer, offer times, book) instead of stalling or asking needless questions?
- Did R sound like a person: no identical repeats, no robotic replies to "um" or noise, no dead ends?
- Was everything R said correct and appropriate for a business?
Answer with exactly one word on the first line: Good (sounds like a real, competent receptionist),
Okay (worked but awkward), or Bad (confusing, wrong, or robotic). Then one short reason."""


def rate(model: str, host: str, turns) -> tuple[str, str]:
    convo = "\n".join(f"C: {h}\nR: {r}" for h, r in turns)
    body = json.dumps({"model": model, "prompt": f"{RUBRIC}\n\nCall:\n{convo}\n\nRating:", "stream": False,
                       "think": False, "options": {"temperature": 0, "num_ctx": 4096}}).encode()
    req = urllib.request.Request(f"{host}/api/generate", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        text = json.loads(r.read())["response"].strip()
    m = re.search(r"\b(Good|Okay|Bad)\b", text, re.I)
    return (m.group(1).capitalize() if m else "Unparsed"), text[:200]


def agreement(judge: list[str], owner: list[str]) -> dict:
    pairs = [(j, o) for j, o in zip(judge, owner) if o]
    exact = sum(j == o for j, o in pairs)
    # Good vs not-Good is the decision that matters most (ship-quality or not).
    binary = sum((j == "Good") == (o == "Good") for j, o in pairs)
    return {"rated_by_owner": len(pairs), "exact": exact, "exact_pct": round(100 * exact / max(1, len(pairs))),
            "good_vs_not_pct": round(100 * binary / max(1, len(pairs)))}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--items"); ap.add_argument("--out")
    ap.add_argument("--model", default="qwen3.6:27b"); ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--agreement", nargs=2)
    a = ap.parse_args(argv)
    if a.agreement:
        j, o = (json.load(open(p, encoding="utf-8")) for p in a.agreement)
        print(json.dumps(agreement([x["rating"] for x in j], o), indent=1))
        return 0
    items = json.load(open(a.items, encoding="utf-8"))["items"]
    out = []
    for i, turns in enumerate(items, 1):
        rating, reason = rate(a.model, a.host, turns)
        out.append({"n": i, "rating": rating, "reason": reason})
        print(i, rating, flush=True)
    json.dump(out, open(a.out, "w", encoding="utf-8"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
