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
    ("book", ["Can I book a call with Alex {day} at {hour}?", "Yes", "Dana Morales"], [(1, "all set"), (2, "dana")]),
    ("book_bye", ["Can I book something {day2} at {hour2}?", "Okay perfect, thanks, bye"], [(1, "take care")]),
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
    ("two_qs", ["How much is a website and how long does it take?"], [(0, "thousand"), (0, "twenty four")]),
    ("math", ["What's two plus two?"], []),
    ("portal", ["What is the charges for client portal app?"], [(0, "portal")]),
    ("donations", ["Can people give donations online and can you do a livestream on Sunday?"], [(0, "donation")]),
    ("nonprofit", ["Do you have a discount for churches?"], [(0, "nonprofit")]),
    ("burned", ["The last web guy took my money and disappeared, why should I trust you?"], [(0, "no commitment")]),
    ("pray", ["Would you pray for me?"], [(0, "kind")]),
    ("revert", ["How much is a website?", "Okay, I will revert"], [(1, "take care")]),
    ("anytime", ["Does it answer at five in the morning?"], [(0, "any time")]),
    ("tell_him", ["Can you take a message?", "Tell him Brenda from Brenda's Nails"], [(1, "brenda")]),
    ("number_q", ["What's the number to reach Alex?"], [(0, "five five one")]),
    ("asr_loop", ["How much is a website? How much is a website? How much is a website?"], [(0, "thousand")]),
    ("es_carmen", ["Hola, buenas, necesito una página web para mi panadería", "Carmen Rosario, Panadería La Dominicana",
                   "¿Alex habla español?", "Bueno, cuídese, que Dios le bendiga"], [(1, "carmen"), (2, "español"), (3, "gracias")]),
    ("es_switch", ["Hi, I have a taco truck", "Mira, mejor en español. ¿La recepcionista puede contestar en español?"], [(1, "español")]),
    ("short_num_book", ["Can I book something {day3} at {hour3}?", "Jenny Park, 732 550 198"], [(1, "jenny")]),

    ("law_privacy", ["Hi, I run a two attorney law practice", "Where are the call recordings stored, and who can access them?"], [(1, "local")]),
    ("liability", ["What if the AI gives someone legal advice by mistake?"], [(0, "advice")]),
    ("pharmacy", ["Hello, is this the pharmacy? I need my refill"], [(0, "pharmacy")]),
    ("split_deposit", ["Can I pay the deposit in two parts?"], [(0, "fifty percent")]),
    ("parent_login", ["I need a website with a parent login for my daycare"], [(0, "log in")]),
    ("es_recep_price", ["Hola, ¿habla español?", "Quiero la recepcionista para mi negocio", "¿Cuánto cuesta?"], [(2, "flexible")]),
    ("book_alex", ["I want to book a call with Alex"], []),

]

DEAD = {"unknown", "lost"}
NONSENSE = {"math"}             # no real answer exists: an honest "don't know" is right
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
HOURS = ["9", "10", "11", "1", "2", "3", "4"]
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
        # Fresh times every run: the lab keeps bookings between runs.
        pick = {"day": random.choice(DAYS), "day2": random.choice(DAYS), "day3": random.choice(DAYS),
                "hour": random.choice(HOURS), "hour2": random.choice(HOURS), "hour3": random.choice(HOURS)}
        turns = [t.format(**pick) if "{" in t else t for t in turns]
        replies = [say(f"{tag}_{name}", t) for t in turns]
        texts = [r.get("reply", "").lower() for r in replies]
        acts = [r.get("action", "") for r in replies]
        checks = {}
        checks["answered"] = name in NONSENSE or not any(a in DEAD for a in acts)
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
