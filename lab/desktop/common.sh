#!/bin/bash
# Shared helpers for desktop lab jobs (WSL). Source me.
set -euo pipefail
LAB="${LAB:-$HOME/pi-model-lab}"
REPO="$LAB/repo"
PY="$LAB/venv/bin/python"
export HF_HOME="$LAB/hf"
export HF_HUB_DISABLE_TELEMETRY=1
export PYTHONUNBUFFERED=1

sync_repo() {  # copy the Windows-side snapshot into WSL
  mkdir -p "$REPO"
  cp -r /mnt/c/Users/AlexS/pi-model-lab/repo/. "$REPO/"
}

wait_gpu() {  # never compete: wait while other processes hold >4 GB
  while true; do
    used=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | head -1)
    if [ "$used" -lt 4096 ]; then return 0; fi
    echo "GPU busy (${used} MiB used); waiting 60s" >&2
    sleep 60
  done
}
