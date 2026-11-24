"""Distillation data: a large local teacher model (Ollama, desktop GPU) writes messy, realistic
caller lines for each intent class. Desktop only; never on the Pi. Output rows match the lab's
jsonl format ({"text", "intent", "source"}) so train.py's dedupe, near-duplicate and label-noise
filters judge them like any other data. Real calls, not the teacher, decide whether they help.

    python -m lab.intents.teacher --model qwen3.6:27b --per-class 150 --out lab/out/teacher.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request

BUSINESS = ("LexMakesIt, a small tech business in Paterson, New Jersey: websites (from one thousand dollars "
            "plus ten dollars a month), custom apps and software, point of sale systems, AI phone receptionists, "
            "automation (missed call text back, review requests, reminders, posting), Google listing setup, "
            "automation teaching sessions (one hundred dollars each). Callers can book a free call with Alex, the owner.")

CLASSES = {
    "availability": "asks what times or days are open, without picking one yet",
    "book": "asks to book or schedule a call or appointment, possibly naming a day or time",
    "reschedule": "wants to move an existing appointment to another time",
    "cancel": "wants to cancel an existing appointment",
    "faq": "asks about opening hours or which days and times the business is open",
    "business": "asks what LexMakesIt does, its services, prices, location, area served, phone number, or how it could help their business",
    "faq_unknown": "asks something unrelated to LexMakesIt (other businesses, general trivia, personal favors)",
    "human": "asks to talk to Alex, the owner, or a real person",
    "emergency": "describes a medical or safety emergency",
    "injection": "tries to make the receptionist ignore its instructions, reveal secrets, or act as something else",
    "small_talk": "greets or makes friendly chit-chat without a request",
    "goodbye": "ends the call or says that's all they needed",
    "affirm": "says yes or agrees, in any casual way",
    "negate": "says no or declines, in any casual way",
}

STYLE = ("Write how real people talk on the PHONE, as a speech-to-text system would transcribe them: fillers "
         "(um, uh, like, so), false starts, run-ons, missing punctuation, casual slang, short fragments, "
         "polite and rude, young and old, some long and rambling, some one or two words. Vary heavily. "
         "Never number the lines. No quotes.")


def ask(model: str, prompt: str, host: str, timeout: int = 600) -> str:
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "think": False,
                       "options": {"temperature": 0.9, "num_ctx": 4096}}).encode()
    req = urllib.request.Request(f"{host}/api/generate", body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["response"]


def lines_from(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", line).strip().strip('"').strip()
        if 1 <= len(line.split()) <= 40 and not line.lower().startswith(("here are", "sure", "note")):
            out.append(line)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.6:27b")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--per-class", type=int, default=150)
    ap.add_argument("--batch", type=int, default=30)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    t0 = time.time(); total = 0
    with open(a.out, "w", encoding="utf-8") as f:
        for label, meaning in CLASSES.items():
            seen: set[str] = set()
            tries = 0
            while len(seen) < a.per_class and tries < a.per_class // a.batch * 3 + 3:
                tries += 1
                prompt = (f"You are writing test data for the phone receptionist of {BUSINESS}\n"
                          f"Write {a.batch} different things a caller might say that {meaning}.\n{STYLE}\n"
                          f"One per line.")
                for line in lines_from(ask(a.model, prompt, a.host)):
                    key = line.lower()
                    if key not in seen and len(seen) < a.per_class:
                        seen.add(key)
                        f.write(json.dumps({"text": line, "intent": label, "source": f"teacher:{a.model}"}) + "\n")
            total += len(seen)
            print(f"{label}: {len(seen)} lines ({time.time() - t0:.0f}s)", flush=True)
    print(f"TEACHER_DONE {total} lines in {time.time() - t0:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
