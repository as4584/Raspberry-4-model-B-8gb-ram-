"""Desktop-only (GPU): generate tool calls for a JSONL split with a HF model (+ optional LoRA adapter) and score them.

python lab/desktop/run_eval.py --model Qwen/Qwen2.5-0.5B-Instruct --revision <sha> --data dev.jsonl \
    --out runs/r3_qwen25_05b_zs [--shots-from train.jsonl --shots 4] [--adapter runs/lora_x/adapter]
Greedy decoding, max_new_tokens=160. Never point --data at the sealed holdout from a training context.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch  # noqa: E402
import transformers  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

from lab.eval import prompt  # noqa: E402
from lab.eval.metrics import score  # noqa: E402

DECODING = {"do_sample": False, "max_new_tokens": 160}


def load_jsonl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()]


def render(tok, msgs, model_id):
    kw = {"tools": prompt.tool_specs(), "add_generation_prompt": True, "tokenize": False}
    if "Qwen3" in model_id:
        kw["enable_thinking"] = False
    return tok.apply_chat_template(msgs, **kw)


def pick_shots(train, k):
    """Deterministic few-shot: first example of each distinct expected tool name, up to k."""
    out, seen = [], set()
    for r in train:
        if r["expected"]["name"] not in seen:
            out.append(r)
            seen.add(r["expected"]["name"])
        if len(out) == k:
            break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--revision", default=None)
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--shots-from", default=None)
    ap.add_argument("--shots", type=int, default=0)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    data = load_jsonl(a.data)[: a.limit or None]
    shots = pick_shots(load_jsonl(a.shots_from), a.shots) if a.shots else []
    tok = AutoTokenizer.from_pretrained(a.model, revision=a.revision)
    tok.padding_side = "left"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(a.model, revision=a.revision, torch_dtype=torch.bfloat16).cuda().eval()
    if a.adapter:
        from peft import PeftModel
        model = PeftModel.from_pretrained(model, a.adapter).eval()
    t0 = time.time()
    raws = []
    for i in range(0, len(data), a.batch):
        chunk = data[i:i + a.batch]
        texts = [render(tok, prompt.messages(r, shots), a.model) for r in chunk]
        enc = tok(texts, return_tensors="pt", padding=True).to("cuda")
        with torch.no_grad():
            gen = model.generate(**enc, **DECODING, pad_token_id=tok.pad_token_id)
        for row in gen[:, enc["input_ids"].shape[1]:]:
            raws.append(tok.decode(row, skip_special_tokens=False).replace(tok.pad_token, "").split(tok.eos_token)[0]
                        .split("<|im_end|>")[0])
    elapsed = time.time() - t0
    with open(out / "outputs.jsonl", "w", encoding="utf-8") as f:
        for r, raw in zip(data, raws):
            f.write(json.dumps({"id": r["id"], "intent": r["intent"], "expected": r["expected"], "raw": raw}) + "\n")
    s = score(list(zip(data, raws)))
    s["config"] = {
        "model": a.model, "revision": a.revision, "adapter": a.adapter,
        "adapter_sha256": _dir_hash(a.adapter) if a.adapter else None,
        "data": a.data, "data_sha256": hashlib.sha256(Path(a.data).read_bytes()).hexdigest(), "n": len(data),
        "shots": a.shots, "shot_ids": [x["id"] for x in shots], "prompt_sha256": prompt.prompt_hash(shots),
        "decoding": DECODING, "batch": a.batch, "dtype": "bfloat16", "seconds": round(elapsed, 1),
        "torch": torch.__version__, "transformers": transformers.__version__, "python": platform.python_version(),
        "gpu": torch.cuda.get_device_name(0),
    }
    (out / "scores.json").write_text(json.dumps(s, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: s[k] for k in ("n", "json_valid", "schema_valid", "exact", "safety")}))


def _dir_hash(d):
    h = hashlib.sha256()
    for f in sorted(Path(d).rglob("*")):
        if f.is_file():
            h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()


if __name__ == "__main__":
    main()
