"""Accuracy of a brain bundle's intent model on the simulated-caller phrasings (rounds 4-10).

    BRAIN_DIR=... python -m lab.intents.eval_sim [lab/intents/sim_rounds.jsonl]

These are project-authored paraphrases of what simulated callers said, never real calls and
never the sealed holdout. 'business' and 'faq' are both answers (the brain's rules route them
the same way), so they count as one group; everything else must match exactly."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

from receptionist.intent_model import load

ANSWER = {"faq", "business"}


def same(a: str, b: str) -> bool:
    return a == b or (a in ANSWER and b in ANSWER)


def main(path: str = "lab/intents/sim_rounds.jsonl") -> None:
    model = load(os.environ["BRAIN_DIR"])
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    wrong, per = [], Counter()
    for r in rows:
        got, conf = model.classify(r["text"])
        ok = same(got, r["label"])
        per[(r["label"], ok)] += 1
        if not ok:
            wrong.append((r["label"], got, round(conf, 2), r["text"]))
    print(json.dumps({"accuracy": round(1 - len(wrong) / len(rows), 3), "n": len(rows),
                      "by_label": {l: f"{per[(l, True)]}/{per[(l, True)] + per[(l, False)]}" for l in sorted({r['label'] for r in rows})},
                      "wrong": wrong}, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:])
