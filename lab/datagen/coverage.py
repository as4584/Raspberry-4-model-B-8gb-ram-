"""Pairwise factor coverage: share of feasible (factor=value, factor=value) pairs, per intent, that occur.

Planned pairs are exactly the pairs that some valid factor assignment can produce
(constraints in generate.valid), so the denominator never counts impossible combinations.
"""
from __future__ import annotations

import itertools
from collections import Counter
from functools import lru_cache

from lab.datagen.generate import INTENT_WEIGHTS, factor_space, valid

CONSTRAINED = ("date_style", "time_style", "duration_style", "edge", "correction")


def _pairset(items) -> set:
    return {frozenset(p) for p in itertools.combinations(items, 2)}


@lru_cache(maxsize=None)
def planned_pairs() -> frozenset:
    planned: set = set()
    for intent in INTENT_WEIGHTS:
        space = factor_space(intent)
        core = [k for k in CONSTRAINED if k in space]
        free = [k for k in space if k not in core]
        core_vals: set = set()
        if core:
            for combo in itertools.product(*(space[k] for k in core)):
                fx = dict(zip(core, combo))
                if valid(intent, fx):
                    items = [("intent", intent)] + list(fx.items())
                    planned |= _pairset(items)
                    core_vals |= set(fx.items())
        free_vals = [(k, v) for k in free for v in space[k]]
        for fv in free_vals:
            planned.add(frozenset({("intent", intent), fv}))
            for cv in core_vals:
                planned.add(frozenset({cv, fv}))
        for a, b in itertools.combinations(free_vals, 2):
            if a[0] != b[0]:
                planned.add(frozenset({a, b}))
    return frozenset(planned)


def pairwise(records: list[dict]) -> dict:
    seen: set = set()
    for r in records:
        items = [("intent", r["intent"])] + sorted(r["factors"].items())
        seen |= _pairset(items)
    planned = planned_pairs()
    hit = planned & seen
    missing = sorted((tuple(sorted(p)) for p in planned - seen))
    return {"planned": len(planned), "covered": len(hit), "coverage": len(hit) / len(planned), "missing": missing}


def value_counts(records: list[dict]) -> dict:
    c: Counter = Counter()
    for r in records:
        c[("intent", r["intent"])] += 1
        c[("expected", r["expected"]["name"])] += 1
        for kv in r["factors"].items():
            c[kv] += 1
    return {f"{k}={v}": n for (k, v), n in sorted(c.items())}


def report_markdown(records: list[dict], title: str) -> str:
    rep = pairwise(records)
    lines = [f"# {title}", "", f"- examples: {len(records)}",
             f"- pairwise coverage: {rep['covered']}/{rep['planned']} = {rep['coverage']:.3f}", "",
             "| factor=value | count |", "|---|---|"]
    lines += [f"| {k} | {n} |" for k, n in value_counts(records).items()]
    if rep["missing"]:
        lines += ["", f"Missing pairs (first 50 of {len(rep['missing'])}):", ""]
        lines += [f"- {a[0]}={a[1]} x {b[0]}={b[1]}" for a, b in rep["missing"][:50]]
    return "\n".join(lines) + "\n"
