# R0 Model & License Audit

**Agent:** Ramona (Laptop 2)
**Sprint:** R0 — Model registry, licensing audit, 2–3 candidates
**Date:** 2026-11-17
**Sources:** HuggingFace model cards, official docs, web research
**Branch:** test/evaluation-harness

---

## Audit Summary

| Model | License | GGUF | Commercial OK | Pi4 aarch64 | Decision |
|-------|---------|------|---------------|------------|---------|
| Qwen/Qwen3-0.6B-GGUF | Apache-2.0 | Official | YES | Likely (llama.cpp) | APPROVED for R1 |
| google/functiongemma-270m-it | Gemma ToU v2026 | Third-party | Conditional | Likely (llama.cpp) | CONDITIONAL — owner review required |
| LiquidAI/LFM2.5-350M | LFM Open v1 ($10M cap) | Official | Conditional | UNCONFIRMED | CONDITIONAL — Pi compat unconfirmed |

---

## Candidate Detail

### 1. Qwen/Qwen3-0.6B-GGUF

**Publisher:** Qwen (Alibaba)
**Source:** https://huggingface.co/Qwen/Qwen3-0.6B-GGUF
**Parameters:** 600M (440M non-embedding)
**License:** Apache-2.0 — confirmed on model card

**What Apache-2.0 means for this project:**
- Commercial use: YES
- Redistribution: YES with attribution and license copy
- Fine-tuning: YES
- No royalties, no revenue caps

**GGUF Status:**
- Official GGUF published by Qwen publisher
- Q8_0 = 639 MB on disk
- llama.cpp compatibility confirmed in official documentation (`llama serve -hf Qwen/Qwen3-0.6B-GGUF:Q8_0`)

**Architecture:**
- 28 layers, Group Query Attention (16 Q heads / 8 KV heads)
- 32,768 token context
- Thinking/non-thinking mode switch (useful: can disable CoT for latency)
- Causal LM — requires structured output prompting or grammar constraints for JSON extraction

**Pi4 8GB RAM fit:**
- Q8_0 = ~800 MB resident (estimated); well within budget
- Q4_K_M ~350MB (lighter option when Pi available)
- aarch64 llama.cpp: standard builds work on Pi4; no special kernel required

**Blockers for R1:** None. Ready for download and Pi latency measurement.

**Verdict:** APPROVED FOR R1

---

### 2. google/functiongemma-270m-it

**Publisher:** Google
**Source:** https://huggingface.co/google/functiongemma-270m-it
**Parameters:** 270M
**License:** Gemma Terms of Use (last modified 1 April 2026)

**What Gemma ToU means:**
- Commercial use: YES (permitted for responsible commercial use)
- Restrictions (Section 3.2):
  - Distributors must pass use restrictions downstream
  - Must provide recipients a copy of the Gemma ToU
  - Must mark modified files and include a Notice file
  - Prohibited Use Policy applies (content safety restrictions)
- Fine-tuning: YES (with attribution requirements)
- Revenue cap: NONE in ToU (unlike LFM)

**Key distinction from Apache-2.0:** Not a standard open-source license. The Gemma ToU is Google's custom terms. It is more permissive than many proprietary licenses but adds downstream obligations not present in Apache-2.0.

**GGUF Status:**
- Official Google GGUF not published by Google directly
- Third-party GGUF available: `ggml-org/functiongemma-270m-it-GGUF` (converted by ggml-org)
- Also: `QuantLLM/functiongemma-270m-it-8bit-gguf`
- Third-party conversion SHA must be verified before use; conversion provenance must be documented

**Architecture:**
- Function-calling fine-tune of Gemma 270M base
- Optimized for tool/function calling in structured output
- Relevant capability: booking intent extraction → JSON output

**Pi4 8GB RAM fit:**
- INT8 GGUF ~300MB; Q4 ~135MB — extremely lightweight, fits easily
- llama.cpp compatible (Gemma architecture is well-supported)

**Blockers for R1:**
1. **Owner approval required** — Gemma ToU section 3.2 and Prohibited Use Policy must be reviewed and acknowledged by project owner before training use is approved in sources.csv
2. **GGUF conversion provenance** — must verify ggml-org conversion SHA and document it in registry
3. Update `docs/data/sources.csv` with model_as_training_resource entry if approved

**Verdict:** CONDITIONAL — approved for inference evaluation only; training use requires owner sign-off on Gemma ToU

---

### 3. LiquidAI/LFM2.5-350M

**Publisher:** Liquid AI
**Source:** https://huggingface.co/LiquidAI/LFM2.5-350M
**Parameters:** 350M
**License:** LFM Open License v1.0 (Apache-2.0 derived)

**What LFM Open License v1.0 means:**
- Commercial use: FREE up to $10M annual revenue
- Above $10M: must contact Liquid AI for a commercial license
- Hobbyists, researchers, non-profits: unrestricted
- Redistribution: permitted (Apache-2.0 base terms)
- Modification/fine-tuning: permitted

**Revenue cap assessment:**
- For this project (research/prototype): free tier applies
- Must document org revenue tier before training use

**GGUF Status:**
- Official GGUF from LiquidAI (confirmed: `LiquidAI/LFM2-350M-GGUF` and `LiquidAI/LFM2-350M-Extract-GGUF` exist; also third-party `damienlusson/LiquidAI_LFM2.5-350M-b2b-llamacpp`)
- NOTE: LFM2 vs LFM2.5 — registry entry says "LFM2.5-350M" but publicly available GGUF may be LFM2-350M. Must verify exact model ID match.

**Architecture:**
- Hybrid (not pure transformer) — Liquid Neural Network variant
- May require custom llama.cpp kernel; standard llama.cpp builds may not support LFM architecture natively
- Liquid AI's hardware partnerships: AMD, Qualcomm, Intel, Samsung — Raspberry Pi (ARM Cortex-A72) not explicitly listed

**Pi4 8GB RAM fit:**
- Size at Q4: ~175MB — fits easily if runtime works
- **UNKNOWN: whether Pi4 aarch64 llama.cpp can run LFM architecture without special build**

**Blockers for R1:**
1. **Pi4 aarch64 compatibility unconfirmed** — must test `llama.cpp` with LFM architecture on Pi before proceeding
2. **Exact model ID** — LFM2 vs LFM2.5 mismatch needs resolution; download the correct GGUF
3. Confirm project annual revenue < $10M for free commercial tier (or document as research/non-commercial)

**Verdict:** CONDITIONAL — approved for research; Pi4 runtime compatibility must be confirmed before R1 download

---

## Rejected Models (from docs/data/sources.csv reference)

**Fluent Speech Commands** — explicitly rejected: CC-BY-NC-ND-4.0 forbids commercial use for training/testing. No re-evaluation needed.

---

## R0 Gate Status

**Gate requirement:** Verifiable model cards, no guesses — PASS for Qwen3; CONDITIONAL for FunctionGemma and LFM2.5

**What must happen before R1:**
1. Owner reviews and signs off on Gemma ToU section 3.2 for FunctionGemma training use
2. LFM2.5 Pi4 llama.cpp compatibility confirmed by running test inference on Pi (can be done during S1/S3)
3. Exact model IDs locked (especially LFM2 vs LFM2.5)
4. GGUF SHA256 hashes recorded in registry when downloaded

**Recommendation for R1:** Start with Qwen3-0.6B-GGUF (approved, no blockers). Evaluate FunctionGemma and LFM2.5 in parallel after blockers cleared.

---

## Sources

- Qwen3-0.6B-GGUF model card: https://huggingface.co/Qwen/Qwen3-0.6B-GGUF
- Google Gemma Terms of Use: https://ai.google.dev/gemma/terms
- Gemma models guide: https://nhimg.org/google-gemma-llm-models-guide
- ggml-org FunctionGemma GGUF: https://huggingface.co/ggml-org/functiongemma-270m-it-GGUF
- LFM2.5-350M on HuggingFace: https://huggingface.co/LiquidAI/LFM2.5-350M
- LFM2.5 blog post: https://www.liquid.ai/blog/introducing-lfm2-5-the-next-generation-of-on-device-ai
- LFM2.5 review (aifoss.dev): https://aifoss.dev/blog/lfm2-5-liquidai-family-review-2026/
