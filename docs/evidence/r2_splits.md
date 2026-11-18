# R2 — Grouped Train/Dev/Holdout Splits (leakage-safe)

**Owner:** Ramona (Laptop 2) · **Date:** 2026-11-17 · **Env:** LAPTOP / MOCK / no keys / no PII / no network

Leakage-safe dataset splitting for the eventual model-training phase, per
Finegan-Dollak et al. (ACL 2018, "query split") and the dual-split
recommendation of Oren et al. (EMNLP Findings 2020).

Files: `research/data_pipeline/splits.py`, `tests/test_splits.py`.
Artifacts: `research/data/splits/{train,dev,holdout}.jsonl`.
Run: `PYTHONPATH=. .venv311/bin/python3.11 -m research.data_pipeline.splits`

## The leakage guard

Each example is grouped into a **family** keyed by its *label* — the booked slot
(`slot:YYYY-MM-DD HH:MM`) or the structured command for keywords (`cmd:yes`,
`cmd:human`, …). All surface variants of one program — across register and
language — share a family, and whole families are assigned to a single split.
Assignment is stratified by intent and deterministic (md5-ordered).

> Guarantee: **zero families straddle train/dev/holdout** (verified in CI). A
> model evaluated on the holdout is tested on programs (slots) it never saw in
> training — generalization, not memorization.

## Two split modes (report both)

- **grouped** (default, persisted): the program/query split above — measures
  generalization to *unseen slots*. Single-program keyword intents (affirm,
  deny, handoff) necessarily land in one split (you cannot split one family);
  they concentrate in `train`.
- **iid**: each example assigned independently — the complement that spreads
  keyword phrasings across splits to measure *phrasing* generalization. Not
  leakage-safe by construction; use only for that purpose.

## Results on the grown set (240 examples, grouped mode)

```
sizes   : train 185  dev 28  holdout 27
ratios  : 0.771 / 0.117 / 0.113
families: 46   straddling_families: []        ✅ zero leakage
langs   : train en85/es85/mixed15 · dev en13/es13/mixed2 · holdout en10/es14/mixed3
intents : every intent present in train; dev/holdout carry the many-program
          booking families (unseen slots) + some unknown
```

## Cross-dataset contamination check

`cross_dataset_overlap(train, gold_eval)` reports families present in BOTH the
training split and the gold eval anchor. Current grown-train vs. gold-eval
overlap = **12 families** (e.g. `cmd:yes`, `slot:2027-12-10 14:00`). These are
expected because the grown set and the gold benchmark were built from the same
seeds.

**Action for the model phase:** keep the gold eval set as a disjoint anchor —
either exclude these 12 families from training, or rebuild the gold anchor from
held-out slots/phrasings the generator never produced. The check is wired so
this is a one-line gate when a model is trained.

## Limitations

- Ratios skew toward `train` (~77%) because single-program keyword intents can
  only occupy one split; booking-heavy intents split cleanly.
- `holdout` here tests unseen *slots*, not unseen *phrasings* — that's the
  grouped split's purpose; use the iid report for phrasing generalization.
