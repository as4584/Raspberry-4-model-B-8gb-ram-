#!/bin/bash
# R4: equal-token-budget LoRA ablation, narrow (random-template) vs diverse (factor-balanced) data.
# Same student, hyperparameters, seed per pair; scored on DEV only.
source "$(dirname "$0")/common.sh"
sync_repo
cd "$REPO"
M=${M:-Qwen/Qwen2.5-0.5B-Instruct}
REVISION=${REVISION:-7ae557604adf67be50417f59c2c2f167def9a775}
BUDGET=${BUDGET:-2000000}
SEEDS=${SEEDS:-"0 1 2"}
for seed in $SEEDS; do
  for arm in narrow diverse; do
    run="$LAB/runs/r4_${arm}_s${seed}"
    if [ ! -f "$run/adapter/adapter_model.safetensors" ]; then
      wait_gpu
      "$PY" lab/desktop/train_lora.py --model "$M" --revision "$REVISION" --train "lab/out/train_${arm}.jsonl" \
        --budget-tokens "$BUDGET" --seed "$seed" --out "$run"
    fi
    if [ ! -f "$run/eval_dev/scores.json" ]; then
      wait_gpu
      "$PY" lab/desktop/run_eval.py --model "$M" --revision "$REVISION" --adapter "$run/adapter" \
        --data lab/out/dev.jsonl --out "$run/eval_dev"
    fi
  done
done
echo R4_DONE
