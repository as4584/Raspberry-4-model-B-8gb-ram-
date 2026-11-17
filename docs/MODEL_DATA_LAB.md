# MODEL & DATA LAB — reconstruct the *principle* of data-efficient learning
Sprint S3A — Owner: **Laptop 2 Ramona** (research/curation/independent evaluations) · Integrator: **Laptop 1 Alex**
Version 1.0 — 2026-11-17

## The inspiration — attribution and limits
Likely reference: **Arlequin AI**, a HackPrinceton 2026 sponsor (not independently confirmed to be the exact speaker the user meant). Public descriptions of its **topological neural network** work emphasize relationships across heterogeneous data instead of just growing large LLMs. Its proprietary architecture and training pipeline are NOT publicly described enough to copy faithfully. **Do not claim we reproduced their TNN**. This sprint experimentally tests the transferable research idea: **carefully selected diverse, difficult, dependable examples may improve generalization at fixed data/compute budgets**.
Evidence and reading:
- https://fr.linkedin.com/company/arlequin-ai (sponsor/Princeton link)
- https://tech.eu/2026/09/10/arlequin-ai-lands-eur28m-to-scale-topological-neural-network-technology/
- https://arxiv.org/abs/2410.15226 (synthetic-data diversity and small-model performance)
- https://ijcai.org/proceedings/2025/928 (D3: Diversity, Difficulty, Dependability)
- https://arxiv.org/abs/2502.01697 (BARE: diversity vs instruction-following quality)
These findings are **background hypotheses**, not proof that our receptionist benefits.

## Research question / falsifiable tests
**H1:** with identical student model, train examples, label quality and training FLOPs, a diversity-selected subset outperforms random or repetitive subset on frozen *unseen combinatorial booking cases*.
**H2:** a limited-budget sample-efficiency curve (250, 500, 1000, 2000 examples) reaches equal strict intent+slot accuracy with fewer training tokens compared with a random baseline.
**H3:** a robust deterministic calendar + tiny model extracting validated structured intent outperforms making a model reason about appointment allocation directly, measured in false confirmations, conflicts and Pi latency.
**H4:** synthetic examples diversified over orthogonal real-world factors generalize better to unseen mixes of language variations/ASR noise/corrections than adding many superficial paraphrases.
Null results are acceptable; no magical generalization claims. Because architectures differ, Arlequin's TNN itself is out of scope for this LLM LoRA prototype.

## Agent objective and outputs
Ramona's agent continuously **as a foreground coding workflow only**, not a scheduled daemon, researches new Pi-feasible models, reads model/dataset cards and licenses, scores candidates, downloads approved sources if permission/bandwidth, builds controlled datasets, runs ablations on capable non-Pi hardware, then submits reproducible experiment PRs. It never trains large models on Pi. It does NOT collect private calls, personal audio or PII without explicit informed consent. No unsupervised unrestricted external crawling or paid usage.
Outputs are: `docs/models/registry.csv`, `docs/data/sources.csv`, `docs/data/coverage_matrix.md`, `research/data_pipeline.py` + tests, `research/experiments/` configs (no model binaries), `reports/model_data/YYYY-MM-DD.md` with linked raw aggregate evidence. Use git-lfs or external object store for huge licensed datasets ONLY after explicit owner decision; do not commit 1M samples/weights or restricted data to public repo.

## 0. Candidate discovery — check real constraints, not stars
Candidate registry fields: model_id, URL, publisher, revision SHA, license and redistribution terms, base/chat/tool-purpose, parameters, formats (GGUF/Safetensors), ARM64/llama.cpp compatibility, context and tokenization, structured JSON/function reliability, RAM + quantized model size measured, first-token latency measured **only on Pi**, token/s, cold load, strict held-out intent+slot accuracy, licensing decision, status PASS/REJECT/RESEARCH.
Seed (no runtime success yet):
- Qwen/Qwen3-0.6B (Apache-2.0; official GGUF: https://huggingface.co/Qwen/Qwen3-0.6B-GGUF) as reproducible general baseline.
- google/functiongemma-270m (check current usage terms, format and task fit; **not automatically approved for commercial redistribution**).
- LiquidAI/LFM2.5-350M (verify exact repository/model card/version, license and compatible kernels; **not approved until evidence**).
Search official Hugging Face model pages, llama.cpp supported architecture/version, licenses, GitHub activity and hardware reports. Prioritize **correct extraction × Pi latency × integration cost**, not parameter count alone or community stars. Reject unsupported GGUF, unsuitable commercial license, hallucinated capabilities, and models that cannot run within Pi thermal/RAM budget. Always state date and provenance. See `docs/models/registry.csv`.

## 1. Public data sources — LEGALITY FIRST
Candidate dataset sources (provisional until subset terms, attribution and redistribution verified):
- Amazon MASSIVE (multilingual intent/slot examples), https://huggingface.co/datasets/AmazonScience/massive, listed CC-BY-4.0; useful for format/intent diversity but NOT a plug-and-play appointment corpus; map labels and attribute.
- Mozilla Common Voice (audio/ASR variety), https://commonvoice.mozilla.org/en, individual releases often CC0; verify the exact selected release and its terms/consent. Do not infer audio accent labels from language alone.
- Authored synthetic appointment conversations from our own deterministic policy and business catalog: source license project-owned, canonical labels generated with reference code; use no real personal identifiers; guard against repetitive templates.
**DO NOT use Fluent Speech Commands for this commercial-receptionist project** without explicit additional rights: publisher says academic only and forbids commercial use even for training/testing.
All sources need manifest: dataset_id, URL, specific revision, license, commercial_use_ok, attribution, allowed derivative/redistribution, personal_data_risk, checksum, language coverage, intended role (ASR/NLU/FAQ), approval_by_owner/date. No approval → cannot enter training.

## 2. The heterogeneous data design — interaction graph, not a pile of paraphrases
Represent each example with factors rather than prose alone:
A. Intent: booking, availability, rescheduling, cancellation, FAQ, human, unknown, multi-intent.
B. Slot: dates (absolute, relative, vague, timezone, DST fold/gap), durations (15/30/60), service, resource, existing booking.
C. Turn history: first turn, clarify, confirmation yes/no, self-correction, interrupted answer, repeated request, earlier proposal.
D. Speaker/text: casual/formal, typo, filler, code-switch English/Spanish (if supported), ASR transcription errors, missing words, contradiction.
E. Calendar state: empty, full, overlap, closing, outage, double caller race, different resources, existing cancellation.
F. Acoustic testing (separate real audio dataset): background noise, near/far mic, SNR, silences, speech rate; only with appropriate license/consent; metadata must not assert unmeasured accent demographics.
G. Risk/critical: privacy/identity, unauthorized cancel, false confirm, emergency/handoff, no cost quote, unsupported fact.
H. Business metadata: hours, holidays, location/timezone, services/prices, FAQ version, phonetic service names.
**Measure factor coverage** (# examples per value, intersections/pairs) and semantic redundancy, not just raw corpus size. Maintain rare/edge case quotas and balanced safe refusal coverage. One training example can mix 3–6 independent factors; preserve ground truth from deterministic state transitions. Use normalized anonymized utterances in prompts.

## 3. Generation / curation / data validation workflow
1. Define typed training/eval JSONL schema before generating examples:
```json
{"id":"syn-001","conversation":[{"role":"user","text":"Could you move my haircut to next Friday? Actually Saturday"}],"calendar_fixture":"calendar_042","reference_now":"2026-11-17T15:00:00-05:00","timezone":"America/New_York","expected":{"intent":"reschedule","start_local":null,"needs_clarification":true,"commit_allowed":false},"factors":["correction","relative_date","missing_booking_ref"],"source_id":"synthetic-v1","license":"project-authored","family_id":"reschedule-correction-missing-ref","split":"train"}
```
2. Create a **reference interpreter/calendar policy** and label generator; oracle ensures answer/error is correct. Data where ground truth cannot be verified goes to manual review, not auto-train.
3. Generate examples by **factor combinations** (pairwise covering array plus adversarial combos) from approved text templates; diversify phrasing with an LLM teacher if available. Teacher model outputs are DRAFTS ONLY; accept after schema validation, dedup, reference check and sampled human audit.
4. Normalize PII to fake values. Exact and fuzzy dedup (token Jaccard/MinHash or embeddings) and check paraphrase-cluster bleed across splits.
5. Source-based restrictions: do not contaminate model training with test labels or evaluate on test examples that trained the model; train/dev split by **family_id + speaker/source** so near-duplicates stay together; hold out **combinations of factors**, not just random sentences.
6. **Evaluation integrity:** Ramona can curate training+dev; the final sealed holdout MUST be stored outside the public repo and unavailable to any training agent/context, curated/run by Alex or separate offline evaluator. Public smoke tests can use a small disclosed eval set but are not unbiased heldout. Keep hashes, counts and split manifest in repo without raw private holdout content.
7. Label audit: ≥50 random sampled cases per source and 100% of critical false-confirmation/identity cases reviewed by an independent checker; unresolved disagreements quarantined.
8. Version data as `dataset_vX`: manifest, generator commit, seeds, source checksums, template hashes, train/dev counts, label audit; no raw model transcripts/real numbers in public git.

## 4. Experiments: compare learning, don't assume it
Conditions with SAME student, hardware, token budget, epochs/optimizer/seed (as appropriate):
E0: baseline model, no tuning + deterministic code.
E1: repeat a small narrow template corpus (e.g., 1k samples).
E2: uniform random curated corpus (same 1k samples).
E3: high-diversity factor-balanced subset (same 1k).
E4: diversity+difficulty+dependability subset (same 1k).
E5: E3/E4 with synthetic paraphrases / teacher + independent human/oracle verification (same token budget).
E6: no tune, structured prompting + FAQ retrieval, to check if training is unnecessary.
Run 3 seeds if compute budget supports, report each; reserve 20–30% of effort for hard negative and safety scenarios. Start with E0/E2/E3 for quickest causal test; defer bigger sweep until clear gain.
Training methods **after** base evaluation: LoRA/QLoRA or small supervised fine-tuning on a GPU laptop *if available*, with pinned transformers/PEFT/TRL versions and memory check. Pi only runs converted/quantized inference. Measure energy/runtime/peak VRAM, trained tokens, strict all-fields exact match, rare-factor coverage and calibration, safety failures, and Pi end-to-end time to first audible reply. More training can hurt safety; reject any candidate that regresses critical booking invariants.

## 5. Metrics / pass-fail and decision thresholds
- Model behavior: strict intent+slot exact match (primary), per-field accuracy, unsafe commitment rate (target ZERO on critical suite), unsupported FAQ answer rate, clarification precision, hallucinated calendar actions (zero), error by factor/intersection.
- Diversity: count of unique factor values/combinations, pairwise factor coverage, semantic cluster entropy (defined in report), duplicate/near-duplicate ratio, source diversity; do **not** use diversity score alone to assert improved learning.
- Efficiency: accurate held-out cases per million training tokens, held-out strict accuracy vs train tokens, GPU hours/VRAM, Pi tokens/sec, TTFT and p95 audible latency, RAM/thermal. Same heldout and conditions for every arm.
- Required evidence: provenance/approval registry, dataset schema validation, contamination tests, seeded reproducibility, comparison of E0 vs E2 vs E3 on same frozen withheld evaluation, negative and out-of-domain safety cases.
- Deployment selection priority: **critical safety PASS → held-out accuracy → Pi memory and audible p95 latency → maintenance/licenses**; prefer no fine-tuning if prompting+rules already meets targets.

## 6. Sprints for Laptop 2 (overlap with Alex's Pi work)
**R0 — Model intelligence:** fill model registry, source IDs, license audit, ranking; shortlist 2–3 and present unknowns. Test gate: every selected model card/revision/terms verifiable, no Pi performance guesses.
**R1 — Data acquisition and taxonomy:** 100% of allowed input sources manifest-approved; define factors/matrix and schemas. Test gate: reject noncommercial-only or no-license entries, fail if PII or missing provenance.
**R2 — Diversity engine:** deterministic factor-combination generator; valid reference labels, near-duplicate filters, split-by-family; target ≥300 distinct curated train/dev examples initially plus safety scenarios, then scale after evaluation. Test gate: ≥95% planned pairwise factor coverage (measured on documented relevant factor pairs) and no train/dev/holdout family leakage. A high coverage number does not guarantee predictive performance.
**R3 — Model baselines:** load shortlisted compatible candidates on Pi via Alex's S1 access (without changing Pi service), run fixed extraction dev set, latency/memory with correct hardware attribution. Test gate: actual raw output/hash.
**R4 — Sample-efficiency ablation:** run E0/E2/E3 under identical train tokens and hardware, LoRA only on approved training hardware; evaluate sealed test via separate evaluator. Test gate: report effect sizes, seeds and safety regression; **a failed hypothesis is legitimate**.
**R5 — Integrate winning candidate:** give Alex a reproducible artifact reference, model SHA/license, conversion procedure, schema and regression tests. Don't commit weights/binaries without explicit redistribution rights. Pi bakeoff decides winner only after G6 measurements.
**R6 — Ongoing test maintenance:** build adversarial dual-stream / collision / noise regression tests and documentation. This is independent work throughout sprints.

## 7. Role coordination & progress reporting
Ramona is **primary model/data researcher** AND independent QA owner for the contracts and high-risk tests; the main S0–S9 gates remain. Alex is deployment and runtime implementer and serves as the **blind heldout custodian** (or delegates to another trusted evaluator). Review one another's PRs; preserve independent eval separation from tuning.
Every research PR: hypothesis; paper/source URLs and license; dataset provenance and coverage; exact experiment config and code SHA; test RED/GREEN; score table by factor; measured vs hypothetical; reusable paths; follow-up.
Progress is not a background automation. The agent works while invoked in its coding CLI and stops when its account's access expires. No fabricated unattended training jobs.

## 8. Prompt for "Laptop 2 Ramona" research mode
"You are Laptop 2 Ramona. Read AGENTS.md, docs/MODEL_DATA_LAB.md, docs/MASTER_PLAN.md, docs/CURRENT_STATE.md, docs/contracts.md and docs/roles/laptop-2-ramona.md. Your first active workstream is model discovery and data efficiency research, paired with adversarial TDD. Audit legally usable data sources and small Pi4 models; generate structured factor-diverse, oracle-validated appointment samples; measure diversity, dedup, contamination and sample efficiency with seeded ablations; make small PRs on test/evaluation-harness. Do not copy proprietary Arlequin AI methods or pretend we reproduced its architecture. Keep final heldout hidden from tuning; ask Alex to run final blinded eval. Write RED tests, make them GREEN, never fabricate Pi benchmark results or train on the Pi. Provide exact results and blockers each session."
