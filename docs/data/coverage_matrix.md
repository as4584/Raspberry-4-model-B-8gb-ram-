# Data diversity / factor coverage matrix — S3A research
**Owner: Laptop 2 Ramona**. This is an initial design; no source approved, data trained or factor-coverage benchmark executed yet. Full method in `docs/MODEL_DATA_LAB.md`.

| Factor | Values to cross | Danger when missing |
|---|---|---|
| Intent | book, availability, cancel, reschedule, FAQ, handoff, unknown, compound | Wrong booking intent |
| Time | exact, vague, relative, absent, DST gap/fold, across midnight | Invalid or false dates |
| Dialogue | yes, no, corrections, clarification, repeated confirmation, barge-in | Unauthorized commits |
| Calendar | free, overlap, closing hours, outage, concurrent caller | Double booking or false success |
| Language | casual, formal, Spanish/English code-switch, filler, typo, ASR errors | Fragile patterns |
| Audio | background noise, low SNR, speech rate, packet jitter, silence | ASR turn failures |
| Identity/privacy | booking reference verified, missing verification, wrong caller, PII | Unsafe cancellation |
| FAQ | verified fact, missing fact, outdated price, contradictory request | Hallucination |

## Data generation prescription
Build factor-combinatorial **reference-labeled** conversations rather than unrelated noisy text. Start 300+ clean development cases. For each row store `family_id`, `source_id`, `license`, `factor_tags`, `expected` structured fields, deterministic seed, `reference_now`, timezone, calendar fixture and human review status. Generate semantic variants but score near-duplicate ratio; preserve edge cases rather than deduping away every uncommon utterance.

Split by scenario family/speaker/source, not sentence randomly. Sealed final holdout is curated outside public repo and inaccessible to the training agent; Alex or another independent evaluator computes final verdict. Ramona may build/dev-test on visible dev data.

## Explicit targets — not yet achieved
Pairwise factor coverage ≥95% over a *declared relevant subset*; source-license and PII compliance 100%; zero train/dev family collision; zero critical unsafe commits. Primary model metric: exact intent and slots on blinded holdout. Compare fixed-cost E0 baseline, E2 random examples, E3 factor-balanced examples, E4 diversity+difficulty+dependability. Better data must win experimentally, not because the prompt says so.
