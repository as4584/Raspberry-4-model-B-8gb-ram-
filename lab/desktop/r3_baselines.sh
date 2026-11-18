#!/bin/bash
# R3: zero-shot and few-shot baselines on the DEV split (never the holdout).
source "$(dirname "$0")/common.sh"
sync_repo
cd "$REPO"
DEV=lab/out/dev.jsonl
TRAIN=lab/out/train_diverse.jsonl
declare -A REV=(
  [Qwen/Qwen2.5-0.5B-Instruct]=7ae557604adf67be50417f59c2c2f167def9a775
  [Qwen/Qwen3-0.6B]=c1899de289a04d12100db370d81485cdf75e47ca
  [Qwen/Qwen2.5-1.5B-Instruct]=989aa7980e4cf806f80c7fef2b1adb7bc71aa306
)
for m in Qwen/Qwen2.5-0.5B-Instruct Qwen/Qwen3-0.6B Qwen/Qwen2.5-1.5B-Instruct; do
  tag=$(basename "$m")
  wait_gpu
  "$PY" lab/desktop/run_eval.py --model "$m" --revision "${REV[$m]}" --data $DEV --out "$LAB/runs/r3_${tag}_zs"
  wait_gpu
  "$PY" lab/desktop/run_eval.py --model "$m" --revision "${REV[$m]}" --data $DEV --out "$LAB/runs/r3_${tag}_fs7" \
    --shots-from $TRAIN --shots 7
done
echo R3_DONE
