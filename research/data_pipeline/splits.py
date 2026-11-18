"""R2 grouped train/dev/holdout splits — Ramona (Laptop 2).

Leakage-safe dataset splitting for model training. The hazard (Finegan-Dollak
et al., ACL 2018, "query split"): if paraphrases of the SAME underlying program
straddle train and test, the model can memorize the mapping and the eval score
measures memorization, not generalization.

Guard here: every example is grouped into a FAMILY keyed by its *label* — the
booked slot, or the structured command for keywords — so all surface variants of
one program (across register and language) share a family. Whole families are
assigned to a single split. We stratify by intent so each split stays
representative, and we verify afterwards that zero families straddle splits.

Also provides a cross-dataset contamination check: shared families between the
training split and the gold eval anchor (the eval set must stay disjoint).

Deterministic (md5-ordered), no model, no network, no PII.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

SPLITS = ("train", "dev", "holdout")
DEFAULT_RATIOS = (0.70, 0.15, 0.15)


def _get(ex, attr):
    return ex[attr] if isinstance(ex, dict) else getattr(ex, attr)


def family_key(ex) -> str:
    """Group paraphrases of the same program. Label-derived, language/register
    agnostic, so e.g. 'book 2027-12-10 14:00', 'I'd like to book ... at 2pm',
    and 'reservar ... a las 2pm' share one family."""
    slot = _get(ex, "expected_slot")
    if slot:
        return f"slot:{slot}"
    cmd = _get(ex, "expected_command")
    if cmd:
        return f"cmd:{cmd}"
    # faq / vague / compound / parse-breaking tier-3: keep each its own family
    return f"{_get(ex, 'intent')}:{_get(ex, 'utterance')}"


def _hash_rank(key: str) -> str:
    return hashlib.md5(key.encode("utf-8")).hexdigest()


def make_splits(examples, ratios=DEFAULT_RATIOS, mode: str = "grouped"
                ) -> dict[str, str]:
    """Return {example_id -> split}, stratified by intent, deterministic.

    mode="grouped" (default): families never straddle splits — the leakage-safe
      program/query split. Measures generalization to UNSEEN programs (slots).
    mode="iid": each example assigned independently — paraphrases of a program
      may straddle. The complement split for measuring phrasing generalization
      (esp. keyword intents, which are a single program each).
    """
    r0, r1, _ = ratios
    assign: dict[str, str] = {}
    by_intent: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for ex in examples:
        # grouped -> one bucket per family; iid -> one bucket per example id
        bucket = family_key(ex) if mode == "grouped" else _get(ex, "id")
        by_intent[_get(ex, "intent")][bucket].append(ex)

    for intent, buckets in by_intent.items():
        ordered = sorted(buckets.items(), key=lambda kv: _hash_rank(kv[0]))
        total = sum(len(v) for _, v in ordered)
        t1, t2 = r0 * total, (r0 + r1) * total
        cum = 0
        for _key, items in ordered:
            split = "train" if cum < t1 else "dev" if cum < t2 else "holdout"
            for ex in items:
                assign[_get(ex, "id")] = split
            cum += len(items)
    return assign


def split_dataset(examples, ratios=DEFAULT_RATIOS, mode: str = "grouped"
                  ) -> dict[str, list]:
    assign = make_splits(examples, ratios, mode)
    out: dict[str, list] = {s: [] for s in SPLITS}
    for ex in examples:
        out[assign[_get(ex, "id")]].append(ex)
    return out


# ---------------------------------------------------------------------------
# Verification & reporting
# ---------------------------------------------------------------------------

def straddling_families(examples, assign: dict[str, str]) -> list[str]:
    """Family keys that appear in more than one split (must be empty)."""
    fam_splits: dict[str, set] = defaultdict(set)
    for ex in examples:
        fam_splits[family_key(ex)].add(assign[_get(ex, "id")])
    return [k for k, s in fam_splits.items() if len(s) > 1]


def cross_dataset_overlap(a, b) -> list[str]:
    """Family keys present in BOTH datasets — e.g. training vs. the gold eval
    anchor. A non-empty result is a contamination risk."""
    fa = {family_key(e) for e in a}
    fb = {family_key(e) for e in b}
    return sorted(fa & fb)


def split_report(examples, ratios=DEFAULT_RATIOS, mode: str = "grouped") -> dict:
    assign = make_splits(examples, ratios, mode)
    parts = split_dataset(examples, ratios, mode)
    total = len(examples)
    report = {
        "mode": mode,
        "total": total,
        "sizes": {s: len(parts[s]) for s in SPLITS},
        "ratios": {s: round(len(parts[s]) / total, 3) if total else 0 for s in SPLITS},
        "families_total": len({family_key(e) for e in examples}),
        "straddling_families": straddling_families(examples, assign),
        "intent_per_split": {
            s: dict(Counter(_get(e, "intent") for e in parts[s])) for s in SPLITS
        },
        "lang_per_split": {
            s: dict(Counter(_get(e, "lang") for e in parts[s])) for s in SPLITS
        },
    }
    return report


def to_jsonl(examples) -> str:
    def rec(e):
        return e if isinstance(e, dict) else asdict(e)
    return "\n".join(json.dumps(rec(e), ensure_ascii=False) for e in examples)


# ---------------------------------------------------------------------------
# CLI — split the grown set, check contamination vs the gold eval anchor
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from research.data_pipeline.diversity_engine import generate_pool, select_diverse
    from research.data_pipeline.nlu_eval import gold_set

    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", default="research/data/splits")
    parser.add_argument("--target", type=int, default=240)
    args = parser.parse_args()

    grown = select_diverse(generate_pool(), args.target)
    # Leakage-safe grouped split is what we persist for training.
    parts = split_dataset(grown, mode="grouped")
    out = Path(args.outdir)
    out.mkdir(parents=True, exist_ok=True)
    for s in SPLITS:
        (out / f"{s}.jsonl").write_text(to_jsonl(parts[s]) + "\n", encoding="utf-8")

    print(f"Wrote grouped (leakage-safe) splits to {args.outdir}/")
    print("--- grouped (program/query) split ---")
    print(json.dumps(split_report(grown, mode="grouped"), indent=2, ensure_ascii=False))
    print("\n--- iid split (phrasing-generalization complement) ---")
    iid = split_report(grown, mode="iid")
    print(json.dumps({k: iid[k] for k in ("mode", "sizes", "ratios",
                                          "intent_per_split")}, indent=2,
                     ensure_ascii=False))
    overlap = cross_dataset_overlap(parts["train"], gold_set())
    print(f"\ntrain vs gold-eval shared families: {len(overlap)}")
    if overlap:
        print("  (contamination risk — keep the gold eval anchor disjoint):")
        for k in overlap[:10]:
            print(f"    {k}")
