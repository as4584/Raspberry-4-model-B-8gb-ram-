# Research-Backed Design for Synthetic Data Generation, Augmentation & Evaluation

**Owner:** Ramona (Laptop 2) · **Date:** 2026-11-17 · **Status:** research synthesis — engine build paused pending approval

This document grounds our planned NLU diversity/augmentation engine and eval
methodology in verifiable academic work, per the directive to not build
generation tooling until it is research-backed. It synthesizes three
independent literature reviews (synthetic data generation; evaluation
methodology; on-device small-LM bilingual NLU).

## 0. Honest framing on "what Princeton / Ivy recommends"

All three reviews looked hard for Princeton- and Ivy-specific guidance for our
exact niche (on-device, bilingual EN/ES, sub-1B, structured-command NLU). The
finding: **there is little to no Princeton-specific published work on synthetic
data generation or small-model NLU for this niche, and none was fabricated.**
The two genuine, verified Princeton anchors are:

- **Min-K% Prob** (contamination detection) — Shi, Ajith, Xia, Huang, Liu,
  Blevins, Chen (Danqi Chen's group), Zettlemoyer. ICLR 2024.
  https://arxiv.org/abs/2310.16789
- **Sheared-LLaMA** (efficient small models via structured pruning) — Xia, Gao,
  Zeng, Chen. Princeton NLP, ICLR 2024. https://arxiv.org/abs/2310.06694 ·
  https://github.com/princeton-nlp/LLM-Shearing

The substantive best practices come from a broader top-tier set: **University
of Washington / AI2** (CheckList, Impossible Distillation, annotation
artifacts), **Stanford CRFM** (HELM, Alpaca/Self-Instruct), **Georgia Tech**
(AttrPrompt), **UIUC** (SuperGen), **EPFL** (grammar-constrained decoding,
CRANE), and dialogue/eval work from Michigan/Yale/Tel Aviv/Google. We cite the
real source for each recommendation rather than force a Princeton label.

## 1. Strongest signals (independently converged across reviews)

1. **Organize generation AND evaluation around CheckList test types** — MFT
   (minimum functionality), INV (label-preserving perturbation — output must
   NOT change), DIR (perturbation that must change the output in a known
   direction). Two of three reviews reached this independently. It maps exactly
   onto our tiers and our safety invariant. — Ribeiro et al., ACL 2020 (Best
   Paper). https://aclanthology.org/2020.acl-main.442/
2. **Two-stage, schema-first generation**: a grammar/state-machine backbone
   emits the *gold structured command + a canonical utterance with correct
   slots*; an LLM/paraphrase layer adds surface diversity on top. The label is
   attached at stage 1 and never re-derived from stage 2 → label-preservation
   guarantee. — SynTOD (https://arxiv.org/abs/2404.14772); M2M self-play
   (https://arxiv.org/abs/1801.04871); Overnight
   (https://aclanthology.org/P15-1129/).
3. **Keep date/time math and all writes deterministic; the model only locates
   and labels.** Sub-1B models are unreliable at temporal primitives. — PRIMETIME
   (https://arxiv.org/html/2504.16155v2); hybrid neural-symbolic pattern.
4. **Measure diversity explicitly; volume ≠ coverage.** Track an embedding-based
   diversity metric to catch mode collapse as data scales. — Vendi Score
   (https://arxiv.org/abs/2210.02410); Diversity Coefficient
   (https://arxiv.org/abs/2306.13840).
5. **Generate-then-filter**: every synthetic item passes a critic that
   re-parses it and confirms the surface still entails the attached command. —
   Impossible Distillation (https://aclanthology.org/2024.naacl-long.250/);
   SuperGen (https://arxiv.org/abs/2202.04538).

## 2. Synthetic data generation & augmentation

- **Attributed/factorized prompts** (vary style, length, persona, formality,
  language) beat plain class-conditional prompts; diversity is the main lever on
  downstream accuracy. — AttrPrompt, NeurIPS 2023.
  https://arxiv.org/abs/2306.15895
- **Span-safety whitelist for augmentation.** EDA (synonym/insert/swap/delete)
  is a cheap floor for coarse classification but is **unsafe on slot-bearing
  text** — random delete/swap can move or destroy the date/time we must book. —
  EDA, EMNLP-IJCNLP 2019. https://aclanthology.org/D19-1670/
- **Back-translation** gives natural paraphrase diversity but introduces
  ~10–15% slot-projection noise on sequence labels; re-align/verify slots after
  round-trip. — Fadaee et al., ACL 2017. https://aclanthology.org/P17-2090/
- **Code-switching must be realistic, not string-concatenation.** Use synthetic
  token-level CS grounded in real CS seeds; validate with an LLM judge, not BLEU.
  — SynCS (https://arxiv.org/abs/2504.01801); Conditioning LLMs to Generate
  Code-Switched Text (https://arxiv.org/abs/2502.12924,
  https://github.com/hitz-zentroa/cs-generation).
- **Distillation from a larger teacher** to label/generate, then train the tiny
  student, is well-precedented. — ZeroGen (https://arxiv.org/abs/2202.07922);
  SuperGen (https://arxiv.org/abs/2202.04538); Self-Instruct/Alpaca
  (https://aclanthology.org/2023.acl-long.754/).
- **Guard against stylistic artifacts/shortcuts** (e.g., "any Spanish ⇒ help",
  "long polite utterance ⇒ human"). Decorrelate style attributes from labels
  during sampling; use DIR tests to catch it. — Annotation Artifacts, NAACL 2018.
  https://aclanthology.org/N18-2017/

## 3. Evaluation methodology

- **Grouped/family splits**: no paraphrase family may straddle train/dev/holdout,
  or you measure memorization, not generalization. Report BOTH an IID split and
  a template-disjoint (compositional) split. — Finegan-Dollak et al., ACL 2018
  (https://aclanthology.org/P18-1033/); Oren et al., EMNLP Findings 2020
  (https://arxiv.org/abs/2010.05647).
- **Contamination probe on any LLM-generated eval item** — exact + near-dup
  n-gram overlap, embedding-similarity dedup, and a Min-K%-Prob likelihood check
  on the generating model; quarantine high-scoring items. — Min-K% (Princeton),
  ICLR 2024 (https://arxiv.org/abs/2310.16789); contamination survey
  (https://arxiv.org/abs/2502.14425).
- **Report multiple metrics, not one headline** — exact-match (our Joint-Goal
  analog) as the gate, plus per-slot F1 and a per-error-type breakdown; JGA
  alone underestimates, slot accuracy alone overestimates. — HELM, TMLR 2023
  (https://arxiv.org/abs/2211.09110); MultiWOZ 2.4
  (https://arxiv.org/abs/2104.00773).
- **Calibration + selective prediction are safety metrics for us.** Enforce
  "never mis-book" via calibrated confidence feeding an abstain/confirm policy;
  report ECE and risk-coverage curves. — Guo et al., ICML 2017.
  https://proceedings.mlr.press/v70/guo17a/guo17a.pdf
- **Code-switch evaluation** has standard benchmarks to template from. —
  GLUECoS (EN–ES included), ACL 2020
  (https://aclanthology.org/2020.acl-main.329.pdf); LinCE
  (https://ritual.uh.edu/lince/).
- **Challenge holdout + human validation of synthetic labels** via
  human-and-model-in-the-loop; report human-vs-synthetic agreement (κ). —
  Dynabench, NAACL 2021. https://aclanthology.org/2021.naacl-main.324/

## 4. On-device small-LM NLU (for S3A, when the model lands)

- **Grammar-constrained decoding (GBNF in llama.cpp) on the final command** makes
  malformed/invented commands structurally impossible — our core safety lever. —
  GCD, EMNLP 2023. https://aclanthology.org/2023.emnlp-main.674/
- **Do not constrain the reasoning trace, only the emitted command** (strict
  format hurts reasoning); reason free, then switch to constrained emission. —
  Let Me Speak Freely (https://arxiv.org/abs/2408.02442); CRANE, ICML 2025
  (https://arxiv.org/abs/2502.09061).
- **Small curated data + PEFT/LoRA goes far**: instruction-tuned small models are
  strong few-shot slot fillers; PEFT reached comparable performance with <6% of
  the data; ~1k clean examples can teach format/behavior. — ILLUMINER
  (https://arxiv.org/abs/2403.17536); LIMA, NeurIPS 2023
  (https://proceedings.neurips.cc/paper_files/paper/2023/file/ac662d74829e4407ce1d126477f4a03a-Paper-Conference.pdf).
- **Train on gold + noisy ASR transcript pairs, with ASR-agnostic noise**, so
  slots survive transcription errors. — AR-NLU, Interspeech 2024
  (https://www.isca-archive.org/interspeech_2024/phung24_interspeech.html);
  Interventional Speech Noise Injection (https://arxiv.org/abs/2410.15609).

## 5. Proposed diversity engine (v1) — each choice traced to a source

> This updates, and is more rigorous than, the engine we paused. It is a
> **proposal for approval**, not yet built.

**Architecture: two-stage, schema-first, label-preserving.**

1. **Backbone (deterministic):** enumerate (intent × slot-values × calendar-state)
   from our existing factor taxonomy → produce `(gold_command, canonical_utterance)`.
   Oracle action derived by running `gold_command` through the real
   `Dialogue`+`BookingStore` (as we already do). [§1.2, SynTOD/M2M/Overnight]
2. **Surface realization & augmentation (factorized by attributes):** language
   (EN / ES / EN-ES code-switch), register (formal/casual/filler/punct/typo),
   ASR-noise level, difficulty tier. [AttrPrompt]
3. **CheckList-typed examples:** MFT = canonical; INV = label-preserving
   perturbations that must yield the **same** command (our augmentation + a
   regression net); DIR = slot-changing perturbations that must **flip** the
   command (new: directly tests we don't ignore the slot). [CheckList]
4. **Span-safety whitelist:** on any utterance containing a date/time/ref, forbid
   destructive EDA ops; allow only slot-preserving ops (filler, punctuation,
   non-slot synonym, connector/code-switch, verified back-translation). [EDA]
5. **Generate-then-filter critic:** re-parse every generated item; drop/flag any
   whose surface no longer entails its attached command. [Impossible Distillation]
6. **Diversity instrumentation:** Vendi Score (multilingual sentence embeddings)
   per tier and per language; alarm on collapse as the set grows. [Vendi]
7. **Grouped splits:** every example carries a `family_id`; emit an IID split and
   a family-disjoint (compositional) split; no family straddles splits.
   [Finegan-Dollak / Oren]
8. **Keep deterministic (unchanged):** all date/time resolution, availability,
   double-book checks, and writes. The engine never asks a model to do date math.
   [PRIMETIME]

**Eval/harness upgrades (fold into `nlu_eval.py`):** add per-slot F1 + error-type
breakdown alongside the existing exact-match gate; add a DIR test suite; keep the
`unsafe == 0` gate; add a Vendi diversity report; add a contamination/dedup check
to run *if/when* any item is LLM-generated. [HELM, Min-K%]

**Deferred to S3A (model phase), documented now:** GBNF-constrained command
emission; reason-free-then-constrain; ASR-noise training pairs; LIMA-style
curated + distilled fine-tuning set. [GCD, CRANE, AR-NLU, LIMA]

## 6. Decisions needed before building

- **LLM in the loop or not for v1?** Everything in §5 steps 1–8 can be done
  **fully deterministically / rule-based** (no external LLM, no new licenses, no
  PII, no contamination risk). The LLM-paraphrase and distillation pieces add
  diversity but introduce contamination, bias, and license questions. Recommend
  **v1 = deterministic schema + rule-based realizers/perturbers + diversity
  metric + grouped splits + DIR tests**, and treat LLM-based augmentation as a
  gated v2 with the contamination checks in place.
- **Vendi Score dependency:** needs a sentence-embedding model. Options: a small
  local multilingual embedder, or a simpler n-gram/self-BLEU diversity proxy with
  zero new deps for v1.

## 7. Confidence & gaps (carried from the reviews)

- **High confidence (verified URLs):** CheckList, Min-K% (Princeton), Sheared-LLaMA
  (Princeton), EDA, Annotation Artifacts, Finegan-Dollak, Oren, HELM, Dynabench,
  GLUECoS, GCD, Let Me Speak Freely, CRANE, ILLUMINER, LIMA, PRIMETIME, AR-NLU,
  Interventional Noise, SynCS, AttrPrompt, SuperGen, ZeroGen, Self-Instruct, M2M,
  Overnight, back-translation (Fadaee/Sennrich), Impossible Distillation.
- **Medium confidence — verify landing page before formal citation:** Vendi Score
  (2210.02410), Diversity Coefficient (2306.13840), MultiWOZ 2.4 (2104.00773),
  Guo et al. PMLR page, SynTOD's exact +37%/+100% figures, MIDAS / COLING-industry
  distillation specifics, contamination survey (2502.14425).
- **Excluded as unverifiable (not cited as load-bearing):** several post-cutoff /
  implausible arXiv IDs surfaced by search (various 26xx.* IDs); a specific
  clinical temporal-pipeline source; on-device 4-bit latency figures. **Benchmark
  quantization/latency on our own Pi 4 + Qwen3-0.6B** rather than trusting those.
