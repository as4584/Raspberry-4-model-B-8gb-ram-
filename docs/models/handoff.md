# Handoff: fine-tuned receptionist tool-caller → Pi benchmark (for Laptop 1 Alex)

Status: **dev-only evidence**. Nothing here is proven on the Pi or on the sealed holdout.
Reviews are same-owner (Ramona left), so this is not independent QA.

## Artifact (on the desktop only, not committed, not uploaded)
See the PR body / `docs/models/r3_r4_results.md` for the exact run, adapter hash and GGUF SHA256s.
Location on desktop `power` (WSL user `lex`): `~/pi-model-lab/gguf/` (`*-Q4_K_M.gguf`, `*-Q8_0.gguf`, `*.SHA256SUMS.txt`).
Base model: `Qwen/Qwen2.5-0.5B-Instruct@7ae557604adf67be50417f59c2c2f167def9a775` (Apache-2.0); the
fine-tune is a derivative and must keep the Apache-2.0 notice. Owner decides on redistribution.

## 1. Seal the holdout first (only you; private seed, outside the repo)
```bash
python -m lab.datagen --split holdout --seed <SECRET> --n 600 --out ~/sealed/holdout.jsonl
```
The CLI refuses holdout paths inside the repository. Holdout template families are disjoint from
train/dev by construction (`lab/datagen/splits.py`); never give the file or seed to a training agent.

## 2. Score the holdout on the desktop (GPU, same harness as dev)
```bash
# in WSL on the desktop, after copying holdout.jsonl to ~/sealed/ there
cd ~/pi-model-lab/repo
../venv/bin/python lab/desktop/run_eval.py --model Qwen/Qwen2.5-0.5B-Instruct \
  --revision 7ae557604adf67be50417f59c2c2f167def9a775 --adapter ~/pi-model-lab/runs/<BEST_RUN>/adapter \
  --data ~/sealed/holdout.jsonl --out ~/sealed/holdout_eval
```
Report `exact` and the `safety` block; any `action_on_injection` > 0 or `success_claim` > 0 is a FAIL.

## 3. Pi benchmark (llama.cpp, CPU)
1. Copy the GGUF to the Pi yourself (`scp` over Tailscale) and verify `sha256sum` against the SUMS file.
2. Use a pinned llama.cpp build on the Pi and record its commit.
3. Measure with the real prompt length (system prompt + tool schema ≈ 1.4k tokens; cache it):
```bash
./llama-bench -m model-Q4_K_M.gguf -p 1450 -n 64 -t 4
./llama-server -m model-Q4_K_M.gguf -c 4096 -t 4 --jinja   # tools via the OpenAI-compatible API
```
4. Record: cold load s, RSS, prompt-eval tok/s, gen tok/s, time to a complete `<tool_call>` (p50/p95 over
   the dev set), and SoC temperature/throttling. Compare Q4_K_M vs Q8_0 accuracy on the same dev subset.
5. A ~1.4k-token prompt on a Pi 4 will likely dominate latency; prompt caching (`--cache-prompt`, a fixed
   system prefix) is the first thing to try. This is a hypothesis, not a measurement.

## Integration rule (unchanged)
The model output is parsed (`lab/eval/parse.py`), validated against `docs/models/tool_call.schema.json`
(`lab/eval/schema.py`), and only then mapped to `Dialogue`/`BookingStore`, which still require explicit
caller confirmation. The model never writes to the DB and never claims success. The tool schema is a
PROPOSAL until you sign it off as a contract.
