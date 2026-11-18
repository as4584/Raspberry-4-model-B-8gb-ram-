"""Desktop-only: one line per LoRA run with training meta + adapter hash (stdlib)."""
import json
import sys
from pathlib import Path

for d in map(Path, sys.argv[1:]):
    m = d / "train_meta.json"
    s = d / "eval_dev" / "scores.json"
    if not m.exists():
        continue
    t = json.loads(m.read_text())
    a = json.loads(s.read_text())["config"].get("adapter_sha256", "") if s.exists() else ""
    print(f"| {d.name} | {t['examples']} | {t['trained_seq_tokens_per_epoch']} | {t['supervised_tokens_per_epoch']} | "
          f"{t['steps']} | {t['seconds']} | {t['peak_vram_gb']} | {t['train_sha256'][:12]} | {t['used_ids_sha256'][:12]} | {a[:16]} |")
