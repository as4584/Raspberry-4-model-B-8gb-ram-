"""Scorecard: scripted conversations through the lab brain (real MiniLM, --exact text, no ASR),
scored on what callers complained about. One number per area, tracked round to round.

    python lab/sim/scorecard.py            # runs against the lab on the Pi (lab/sim/call.py)

Checks per conversation (each a pass/fail):
  answered   - no dead end: never 'unknown'/'lost' where a real answer exists
  no_repeat  - never the same reply twice in a row
  kept_lead  - a name/number the caller gave appears in her read-back or confirmation
  language   - a Spanish call is answered in Spanish after the greeting
  stayed     - never hung up on a caller who said something real
  expect     - the conversation-specific check (substring in a given turn's reply)
"""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable

# (name, turns, [(turn_index, substring the reply must contain, lowercase)])
CONVERSATIONS = [
    ("price", ["How much is a website?"], [(0, "thousand")]),
    ("deposit", ["Is there a deposit or a payment plan?"], [(0, "fifty percent")]),
    ("own_domain", ["Who owns the website, me or you?"], [(0, "own your domain")]),
    ("contract", ["Am I locked into a contract?"], [(0, "no contract")]),
    ("refund", ["What if I don't like it, do I get my money back?"], [(0, "refund")]),
    ("timeline", ["How long does it take to build?"], [(0, "twenty four hours")]),
    ("portfolio", ["Can I see some sites you've built?"], [(0, "private")]),
    ("legit", ["How do I know you're legit?"], [(0, "free call")]),
    ("pitch", ["Can you answer phones for my barbershop?"], [(0, "talking to one")]),
    ("pitch_price", ["Could a receptionist like you work for my salon?", "How much?"], [(1, "flexible")]),
    ("hipaa", ["Is it HIPAA compliant?"], [(0, "hipaa")]),
    ("square", ["Does it work with Square?"], [(0, "connect")]),
    ("haggle", ["Can you do it for six hundred?"], [(0, "budget")]),
    ("fixed_income", ["I'm retired on a fixed income, what would it cost?"], [(0, "budget")]),
    ("solo", ["Is it just one guy?"], [(0, "directly")]),
    ("cards", ["Can my customers pay with a credit card on the site?"], [(0, "card")]),
    ("ordering", ["Can people order food online from my site?"], [(0, "online ordering")]),
    ("maintenance", ["I'm not good with computers, is that a problem?"], [(0, "not at all")]),
    ("message", ["Can you take a message?", "Kim Tran, 201 555 0177", "Tell him my site is down, it's urgent", "That's it, thanks"],
     [(1, "two oh one"), (2, "message"), (3, "take care")]),
    ("one_breath", ["Have Alex call me, 973 555 0142, Walter Brennan", "About a website for my woodworking"], [(0, "walter"), (1, "walter")]),
    ("book", ["Can I book a call with Alex Friday at 10?", "Yes", "Dana Morales"], [(1, "all set"), (2, "dana")]),
    ("book_bye", ["Can I book something Thursday at 11?", "Okay perfect, thanks, bye"], [(1, "take care")]),
    ("slower", ["How much is a website?", "Could you say that slower?"], [(1, "thousand")]),
    ("spam", ["This is a call about your car's extended warranty, press 1"], [(0, "goodbye")]),
    ("spam2", ["This is your final notice, press one now"], [(0, "goodbye")]),
    ("no_hangup_real", ["My website is down and I'm losing sales", "flurb", "glorp", "zib zab"], []),
    ("es_full", ["Hola, necesito una página web para mi restaurante", "¿Cuánto cuesta?", "¿Hay que pagar un depósito?",
                 "Me llamo Marisol Peña, mi número es 973 555 2281", "Quiero pedidos en línea", "No, gracias, adiós"],
     [(0, "hola"), (1, "mil dólares"), (2, "cincuenta"), (3, "marisol"), (4, "alex"), (5, "gracias")]),
    ("es_question", ["Do you speak Spanish?"], [(0, "español")]),
    ("thats_it", ["How much is a website?", "Yeah that's it, thanks"], [(1, "take care")]),
    ("goodbye_god", ["Thank you dear, God bless, goodbye"], [(0, "god bless")]),
]

DEAD = {"unknown", "lost"}
SPANISH_HINT = ("á", "é", "í", "ó", "ú", "ñ", "¿", "hola", "gracias", "alex le")


def say(session: str, text: str) -> dict:
    out = subprocess.run([PY, str(HERE / "call.py"), "say", session, text, "--exact"], capture_output=True, timeout=120,
                         env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    return json.loads(out.stdout.decode("utf-8"))


def run() -> dict:
    tag = f"sc{random.randrange(10**6)}"
    rows, totals = [], {"answered": 0, "no_repeat": 0, "kept_lead": 0, "language": 0, "stayed": 0, "expect": 0}
    counts = dict.fromkeys(totals, 0)
    for name, turns, expects in CONVERSATIONS:
        replies = [say(f"{tag}_{name}", t) for t in turns]
        texts = [r.get("reply", "").lower() for r in replies]
        acts = [r.get("action", "") for r in replies]
        checks = {}
        checks["answered"] = not any(a in DEAD for a in acts)
        checks["no_repeat"] = all(texts[i] != texts[i - 1] for i in range(1, len(texts)))
        spanish = any(h in turns[0].lower() for h in ("hola", "necesito", "página"))
        if spanish:
            checks["language"] = all(any(h in t for h in SPANISH_HINT) for t in texts)
        real = not name.startswith("spam")
        checks["stayed"] = (not real) or not any(a == "hangup" for a in acts)
        if any(ch.isdigit() for t in turns for ch in t) and name not in ("haggle",) and real:
            checks["kept_lead"] = any(w in " ".join(texts) for w in ("oh", "nine", "two", "nueve", "dos", "all set"))
        misses = [f"t{i}:{want}" for i, want in expects if want not in texts[i]]
        checks["expect"] = not misses
        for k, v in checks.items():
            counts[k] += 1
            totals[k] += int(v)
        rows.append({"name": name, "checks": checks, "misses": misses, "actions": acts})
    score = {k: round(100 * totals[k] / counts[k]) for k in totals if counts[k]}
    overall = round(sum(sum(r["checks"].values()) for r in rows) * 100 / sum(len(r["checks"]) for r in rows))
    return {"overall": overall, "by_check": score, "failures": [r for r in rows if not all(r["checks"].values())]}


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=1, ensure_ascii=False))
