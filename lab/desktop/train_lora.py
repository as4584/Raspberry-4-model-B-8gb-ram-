"""Desktop-only (GPU): LoRA fine-tune on a train JSONL under a fixed TOKEN budget, loss on the assistant tool call only.

python lab/desktop/train_lora.py --model Qwen/Qwen2.5-0.5B-Instruct --revision <sha> --train train_diverse.jsonl \
    --budget-tokens 1500000 --seed 0 --out runs/lora_diverse
Equal-token-budget: examples are shuffled with --seed and taken until the cumulative full-sequence token
count reaches --budget-tokens, so arms with different data get the same number of trained tokens.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import peft  # noqa: E402
import torch  # noqa: E402
import transformers  # noqa: E402
from peft import LoraConfig, get_peft_model  # noqa: E402
from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: E402

from lab.eval import prompt  # noqa: E402

HP = {"lora_r": 16, "lora_alpha": 32, "lora_dropout": 0.05,
      "target_modules": ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
      "lr": 2e-4, "epochs": 2, "batch": 16, "micro_batch": 2, "warmup_ratio": 0.05, "weight_decay": 0.0, "max_len": 2048}


def encode(tok, rec, model_id, layout="v1"):
    kw = {"tools": prompt.tool_specs(), "tokenize": False}
    if "Qwen3" in model_id:
        kw["enable_thinking"] = False
    msgs = prompt.messages(rec, layout=layout)
    prefix = tok.apply_chat_template(msgs, add_generation_prompt=True, **kw)
    full = tok.apply_chat_template(msgs + [{"role": "assistant", "content": prompt.target(rec)}], **kw)
    assert full.startswith(prefix), "chat template prefix mismatch"
    p_ids = tok(prefix, add_special_tokens=False)["input_ids"]
    f_ids = tok(full, add_special_tokens=False)["input_ids"]
    labels = [-100] * len(p_ids) + f_ids[len(p_ids):]
    return f_ids[: HP["max_len"]], labels[: HP["max_len"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--revision", default=None)
    ap.add_argument("--train", required=True)
    ap.add_argument("--budget-tokens", type=int, required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--prompt-layout", choices=prompt.LAYOUTS, default="v1")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    random.seed(a.seed)
    torch.manual_seed(a.seed)
    tok = AutoTokenizer.from_pretrained(a.model, revision=a.revision)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    recs = [json.loads(l) for l in Path(a.train).read_text(encoding="utf-8").splitlines() if l.strip()]
    random.Random(a.seed).shuffle(recs)
    examples, used, total, sup = [], [], 0, 0
    for r in recs:
        ids, labels = encode(tok, r, a.model, a.prompt_layout)
        if total + len(ids) > a.budget_tokens:
            break
        examples.append((ids, labels))
        used.append(r["id"])
        total += len(ids)
        sup += sum(x != -100 for x in labels)
    if total < 0.98 * a.budget_tokens:
        raise SystemExit(f"train file too small for budget: {total} < {a.budget_tokens}")
    model = AutoModelForCausalLM.from_pretrained(a.model, revision=a.revision, torch_dtype=torch.bfloat16).cuda()
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(r=HP["lora_r"], lora_alpha=HP["lora_alpha"], lora_dropout=HP["lora_dropout"],
                                             target_modules=HP["target_modules"], task_type="CAUSAL_LM"))
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=HP["lr"], weight_decay=HP["weight_decay"])
    steps = HP["epochs"] * math.ceil(len(examples) / HP["batch"])
    warm = max(1, int(HP["warmup_ratio"] * steps))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warm) * max(0.0, 0.5 * (1 + math.cos(math.pi * min(1.0, s / steps)))))
    log = open(out / "train_log.jsonl", "w", encoding="utf-8")
    t0, step = time.time(), 0
    model.train()
    for ep in range(HP["epochs"]):
        order = list(range(len(examples)))
        random.Random(a.seed * 1000 + ep).shuffle(order)
        for b in range(0, len(order), HP["batch"]):
            batch = [examples[i] for i in order[b:b + HP["batch"]]]
            L = max(len(x[0]) for x in batch)
            ids = torch.full((len(batch), L), tok.pad_token_id)
            lab = torch.full((len(batch), L), -100)
            att = torch.zeros((len(batch), L), dtype=torch.long)
            for j, (x, y) in enumerate(batch):
                ids[j, : len(x)] = torch.tensor(x)
                lab[j, : len(y)] = torch.tensor(y)
                att[j, : len(x)] = 1
            mb, n_sup = HP["micro_batch"], int((lab != -100).sum())
            loss_val = 0.0
            for k in range(0, len(batch), mb):  # grad accumulation; token-weighted mean over the batch
                sl = slice(k, k + mb)
                logits = model(input_ids=ids[sl].cuda(), attention_mask=att[sl].cuda()).logits[:, :-1].float()
                tgt = lab[sl, 1:].cuda()
                l = torch.nn.functional.cross_entropy(logits.reshape(-1, logits.size(-1)), tgt.reshape(-1),
                                                      ignore_index=-100, reduction="sum") / n_sup
                l.backward()
                loss_val += l.item()
            loss = torch.tensor(loss_val)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % 10 == 0 or step == steps:
                log.write(json.dumps({"step": step, "epoch": ep, "loss": round(loss.item(), 5),
                                      "lr": sched.get_last_lr()[0]}) + "\n")
                log.flush()
    model.save_pretrained(out / "adapter")
    meta = {"model": a.model, "revision": a.revision, "train": a.train,
            "train_sha256": hashlib.sha256(Path(a.train).read_bytes()).hexdigest(),
            "seed": a.seed, "budget_tokens": a.budget_tokens, "trained_seq_tokens_per_epoch": total,
            "supervised_tokens_per_epoch": int(sup), "examples": len(examples), "steps": steps, "hp": HP,
            "used_ids_sha256": hashlib.sha256("\n".join(used).encode()).hexdigest(),
            "prompt_sha256": prompt.prompt_hash(layout=a.prompt_layout),
            "prompt_layout": a.prompt_layout, "seconds": round(time.time() - t0, 1),
            "peak_vram_gb": round(torch.cuda.max_memory_allocated() / 1e9, 2),
            "torch": torch.__version__, "transformers": transformers.__version__, "peft": peft.__version__}
    (out / "train_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
