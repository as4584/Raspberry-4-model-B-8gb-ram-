"""R2 per-slot metrics & error-type breakdown — Ramona (Laptop 2).

Exact-command accuracy is the gate, but it hides *where* a parser fails. HELM
(Liang et al., TMLR 2023) argues for multiple metrics over a single headline;
task-oriented NLU convention is to report per-slot F1 plus an error-type
breakdown alongside joint accuracy. This module adds that diagnostic layer over
the existing benchmark.

Slots for this domain: date, time, duration. For each booking example we compare
the slots the parser produced against the gold slots and compute:

  * micro precision / recall / F1 over (slot_type:value) pairs,
  * per-slot F1 (date / time / duration),
  * an error-type histogram (correct / missed_booking / wrong_date /
    wrong_time / wrong_duration).

Deterministic, no model, no network, no PII.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from research.data_pipeline.behavioral import _signature

_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_TIME = re.compile(r"\b(\d{2}:\d{2})\b")
_DUR = re.compile(r"\d{2}:\d{2}\s+(15|30|60)\b")


def parse_slots(command: str) -> dict:
    slots: dict[str, str] = {}
    if command is None:
        return slots
    d = _DATE.search(command)
    t = _TIME.search(command)
    u = _DUR.search(command)
    if d:
        slots["date"] = d.group(1)
    if t:
        slots["time"] = t.group(1)
    if u:
        slots["duration"] = u.group(1)
    return slots


def _f1(tp: int, fp: int, fn: int) -> dict:
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4)}


@dataclass
class SlotReport:
    analyzed: int
    micro: dict
    per_slot: dict
    error_types: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return self.__dict__


def slot_analysis(normalize_fn, examples) -> SlotReport:
    tp = fp = fn = 0
    per = {s: [0, 0, 0] for s in ("date", "time", "duration")}   # tp, fp, fn
    errors: Counter = Counter()
    analyzed = 0

    for ex in examples:
        if getattr(ex, "intent", None) != "book":
            continue
        gold_cmd = ex.expected_command or (
            f"book {ex.expected_slot}" if ex.expected_slot else None)
        # tier-3 (no gold slot) or non-booking targets are out of scope here
        if not gold_cmd or ex.expected_action != "confirm_required":
            continue
        gold = parse_slots(gold_cmd)
        if not gold:
            continue
        analyzed += 1

        action, _ = _signature(normalize_fn, ex.utterance)
        cmd = normalize_fn(ex.utterance)
        pred = parse_slots(cmd) if action == "confirm_required" else {}

        gset = {f"{k}:{v}" for k, v in gold.items()}
        pset = {f"{k}:{v}" for k, v in pred.items()}
        tp += len(gset & pset)
        fp += len(pset - gset)
        fn += len(gset - pset)

        for s in per:
            g, p = gold.get(s), pred.get(s)
            if g is not None and p == g:
                per[s][0] += 1
            else:
                if p is not None:
                    per[s][1] += 1
                if g is not None:
                    per[s][2] += 1

        if action != "confirm_required":
            errors["missed_booking"] += 1
        elif pred == gold:
            errors["correct"] += 1
        elif pred.get("date") != gold.get("date"):
            errors["wrong_date"] += 1
        elif pred.get("time") != gold.get("time"):
            errors["wrong_time"] += 1
        elif pred.get("duration") != gold.get("duration"):
            errors["wrong_duration"] += 1
        else:
            errors["other"] += 1

    return SlotReport(
        analyzed=analyzed,
        micro=_f1(tp, fp, fn),
        per_slot={s: _f1(*per[s]) for s in per},
        error_types=dict(errors),
    )
