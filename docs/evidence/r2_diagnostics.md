# R2 — Diagnostics: Per-Slot F1 & Near-Duplicate Detection

**Owner:** Ramona (Laptop 2) · **Date:** 2026-11-17 · **Env:** LAPTOP / MOCK / no keys / no PII / no network

Two hardware-free diagnostics added to the data/eval pipeline, both
research-backed (HELM multi-metric reporting; contamination survey
exact+near-dup+likelihood hygiene).

## 1. Per-slot F1 + error-type breakdown

`research/data_pipeline/slot_metrics.py`, `tests/test_slot_metrics.py`.

Beyond the exact-match gate, reports where a parser fails: micro
precision/recall/F1 over `(slot_type:value)` pairs, per-slot F1 (date / time /
duration), and an error-type histogram (correct / missed_booking / wrong_date /
wrong_time / wrong_duration).

Rule-based baseline:

```
gold set : analyzed 32  micro F1 1.0  date/time/duration F1 1.0  (all correct)
grown set: analyzed 157 micro F1 1.0  date/time/duration F1 1.0  (all correct)
```

Guard tests prove the breakdown flags a slot-blind parser (wrong_date/time,
F1 < 1) and a never-books parser (missed_booking, recall 0) — so a future
regression surfaces as a specific error class, not just a dropped headline.

## 2. Near-duplicate detection

`research/data_pipeline/dedup.py`, `tests/test_dedup.py`.

Flags surface-similar pairs (cosine over the local n-gram embedder) within a
dataset and across datasets. Complements the family-overlap check in `splits.py`
with a surface-level contamination signal.

```
grown set (240, threshold 0.92):
  near-dup pairs 5 · clusters 3 · effective_unique 235/240   (low redundancy)
  flagged pairs are meaningful: en/es cancel↔cancelar (0.989),
  duration variants of the same slot (~0.94)

grown ↔ gold eval anchor: 72 near-dup pairs
  corroborates the 12 shared families (splits.py) — the gold anchor overlaps
  the grown training set and MUST be kept disjoint before training.
```

**Action for the model phase:** before training, run `dedup.near_duplicates_across(train, gold_eval)`
and `splits.cross_dataset_overlap(train, gold_eval)`; exclude the overlapping
items from training or rebuild the gold anchor from held-out slots/phrasings.

## Notes
- Both use the dependency-free lexical embedder; thresholds are tuned for it
  (0.92 default). A semantic `sentence-transformers` backend would shift the
  scale — re-tune if installed.
- The grown set's high `effective_unique` (235/240) confirms the diversity
  engine's selection already keeps redundancy low.
