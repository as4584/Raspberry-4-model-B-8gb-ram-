"""Train the tiny brain's intent model on filtered, diverse data (run on the desktop GPU).

    python -m lab.intents.train --lab-train lab/out/train_diverse.jsonl --lab-dev lab/out/dev.jsonl \
        --out /mnt/c/Users/AlexS/brain-bundle-v2 [--real real_eval.jsonl]

Data: the lab's 4,000 synthetic receptionist phrases + lab/intents (business + conversational),
then filtered: (1) normalised + cross-label dedupe, (2) near-duplicate removal in embedding
space (cos > 0.97 within a class), (3) out-of-fold label-noise removal (a model that never saw
an example gives its label < 5%), (4) per-class cap. Two candidates are trained and both are
scored on held-out data the training never saw: A = frozen MiniLM + linear head, B = MiniLM
fine-tuned end to end. The sealed holdout is never read.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

import numpy as np
import torch

from lab.intents.generate import CLASSES, balance, dedupe, generate, normalise

ENCODER = "sentence-transformers/all-MiniLM-L6-v2"


def load_jsonl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]


class Encoder(torch.nn.Module):
    def __init__(self, m):
        super().__init__(); self.m = m

    def forward(self, ids, mask):
        h = self.m(input_ids=ids, attention_mask=mask).last_hidden_state
        mf = mask.unsqueeze(-1).float()
        return torch.nn.functional.normalize((h * mf).sum(1) / mf.sum(1), dim=-1)


def embed(enc, tok, texts, dev, grad=False):
    out = []
    for i in range(0, len(texts), 128):
        b = tok(texts[i:i + 128], padding=True, truncation=True, max_length=64, return_tensors="pt").to(dev)
        with torch.set_grad_enabled(grad):
            out.append(enc(b["input_ids"], b["attention_mask"]))
    return torch.cat(out)


def train_head(X, y, n, epochs=400, lr=0.05):
    W = torch.nn.Linear(X.shape[1], n).to(X.device)
    opt = torch.optim.Adam(W.parameters(), lr=lr)
    for _ in range(epochs):
        opt.zero_grad()
        (torch.nn.functional.cross_entropy(W(X), y) + 1e-4 * W.weight.pow(2).sum()).backward()
        opt.step()
    return W


def near_dup_filter(rows, X, thresh=0.97):
    keep = []
    by = {}
    for i, r in enumerate(rows):
        by.setdefault(r["intent"], []).append(i)
    for c, idx in by.items():
        kept = []
        for i in idx:
            if kept and float((X[kept] @ X[i]).max()) > thresh:
                continue
            kept.append(i)
        keep += kept
    return sorted(keep)


def oof_noise_filter(X, y, n, folds=5, floor=0.05, seed=0):
    idx = list(range(len(y))); random.Random(seed).shuffle(idx)
    p_own = torch.zeros(len(y))
    for f in range(folds):
        test = idx[f::folds]; tr = [i for i in idx if i not in set(test)]
        W = train_head(X[tr], y[tr], n, epochs=200)
        with torch.no_grad():
            p = torch.softmax(W(X[test]), -1)
        p_own[test] = p[torch.arange(len(test)), y[test]].cpu()
    return [i for i in range(len(y)) if p_own[i] >= floor], int((p_own < floor).sum())


def accuracy(W, X, y):
    with torch.no_grad():
        return float((W(X).argmax(1) == y).float().mean())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab-train", required=True); ap.add_argument("--lab-dev", required=True)
    ap.add_argument("--out", required=True); ap.add_argument("--real", default="")
    ap.add_argument("--per-class", type=int, default=500); ap.add_argument("--cap", type=int, default=650)
    ap.add_argument("--extra", nargs="*", default=[], help="more training jsonl (e.g. teacher distillation data)")
    ap.add_argument("--skip-b", action="store_true", help="only the frozen-encoder candidate (fast)")
    a = ap.parse_args(argv)
    torch.manual_seed(0)
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    from transformers import AutoModel, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(ENCODER)
    idx = {c: i for i, c in enumerate(CLASSES)}

    lab = [{"text": r["text"], "intent": r["intent"], "source": "lab"} for r in load_jsonl(a.lab_train)]
    extra = [{"text": r["text"], "intent": r["intent"], "source": r.get("source", "extra")} for p in a.extra for r in load_jsonl(p)]
    train = dedupe(lab + extra + generate(seed=11, per_class=a.per_class))
    seen = {normalise(r["text"]) for r in train}
    held_new = [r for r in dedupe(generate(seed=99, per_class=60)) if normalise(r["text"]) not in seen]
    held_lab = [{"text": r["text"], "intent": r["intent"]} for r in load_jsonl(a.lab_dev)]
    real = load_jsonl(a.real) if a.real else []

    base = Encoder(AutoModel.from_pretrained(ENCODER)).to(dev).eval()
    X = embed(base, tok, [r["text"] for r in train], dev)
    keep = near_dup_filter(train, X)
    n_near = len(train) - len(keep)
    train = [train[i] for i in keep]; X = X[keep]
    y = torch.tensor([idx[r["intent"]] for r in train], device=dev)
    keep, n_noise = oof_noise_filter(X, y, len(CLASSES))
    train = balance([train[i] for i in keep], cap=a.cap, seed=0)
    print(f"data: {len(lab)} lab + generated -> near-dups removed {n_near}, label-noise removed {n_noise}, "
          f"final {len(train)}", flush=True)

    def sets(enc):
        out = {}
        for name, rows in (("held_new", held_new), ("lab_dev", held_lab), ("real", real)):
            if rows:
                out[name] = (embed(enc, tok, [r["text"] for r in rows], dev),
                             torch.tensor([idx[r["intent"]] for r in rows], device=dev))
        return out

    texts = [r["text"] for r in train]
    y = torch.tensor([idx[r["intent"]] for r in train], device=dev)
    # Candidate A: frozen encoder + head
    XA = embed(base, tok, texts, dev)
    WA = train_head(XA, y, len(CLASSES))
    scores = {"A": {k: round(accuracy(WA, *v), 4) for k, v in sets(base).items()}}
    if a.skip_b:
        print("scores", json.dumps(scores), flush=True)
        return finish(a, tok, base, WA, "A", scores, train, n_near, n_noise)
    # Candidate B: fine-tune encoder + head end to end (small LR, 3 epochs)
    ft = Encoder(AutoModel.from_pretrained(ENCODER)).to(dev).train()
    head = torch.nn.Linear(384, len(CLASSES)).to(dev)
    opt = torch.optim.AdamW([{"params": ft.parameters(), "lr": 2e-5}, {"params": head.parameters(), "lr": 1e-3}])
    order = list(range(len(texts)))
    for ep in range(3):
        random.Random(ep).shuffle(order)
        for i in range(0, len(order), 32):
            bi = order[i:i + 32]
            e = embed(ft, tok, [texts[j] for j in bi], dev, grad=True)
            loss = torch.nn.functional.cross_entropy(head(e * 20.0), y[bi])
            opt.zero_grad(); loss.backward(); opt.step()
    ft.eval()
    WB = train_head(embed(ft, tok, texts, dev), y, len(CLASSES))
    scores["B"] = {k: round(accuracy(WB, *v), 4) for k, v in sets(ft).items()}
    print("scores", json.dumps(scores), flush=True)

    pick = "B" if sum(scores["B"].values()) > sum(scores["A"].values()) else "A"
    enc, W = (ft, WB) if pick == "B" else (base, WA)
    return finish(a, tok, enc, W, pick, scores, train, n_near, n_noise)


def finish(a, tok, enc, W, pick, scores, train, n_near, n_noise):
    out = Path(a.out); shutil.rmtree(out, ignore_errors=True); out.mkdir(parents=True)
    tok.save_pretrained(out / "tokenizer")
    np.savez(out / "intent_head.npz", W=W.weight.detach().cpu().numpy(), b=W.bias.detach().cpu().numpy(),
             classes=np.array(CLASSES))
    enc = enc.cpu().eval()
    ex = tok(["hello there"], return_tensors="pt")
    torch.onnx.export(enc, (ex["input_ids"], ex["attention_mask"]), str(out / "encoder.onnx"),
                      input_names=["input_ids", "attention_mask"], output_names=["embedding"],
                      dynamic_axes={"input_ids": {0: "b", 1: "t"}, "attention_mask": {0: "b", 1: "t"},
                                    "embedding": {0: "b"}}, opset_version=17, dynamo=False)
    from onnxruntime.quantization import QuantType, quantize_dynamic
    quantize_dynamic(str(out / "encoder.onnx"), str(out / "encoder.int8.onnx"), weight_type=QuantType.QInt8)
    (out / "encoder.onnx").unlink()
    meta = {"encoder": ENCODER, "license": "Apache-2.0", "candidate": pick, "scores": scores, "classes": list(CLASSES),
            "train_size": len(train), "near_dups_removed": n_near, "label_noise_removed": n_noise}
    (out / "meta.json").write_text(json.dumps(meta, indent=1))
    print("picked", pick, "->", out, flush=True)


if __name__ == "__main__":
    main()
