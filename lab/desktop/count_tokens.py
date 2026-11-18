"""Desktop-only: token statistics of the training encoding per train file (to choose an equal token budget)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from transformers import AutoTokenizer  # noqa: E402

from lab.desktop.train_lora import encode  # noqa: E402

model, revision, *files = sys.argv[1:]
tok = AutoTokenizer.from_pretrained(model, revision=revision)
for f in files:
    recs = [json.loads(l) for l in Path(f).read_text(encoding="utf-8").splitlines()]
    lens = [encode(tok, r, model) for r in recs]
    tot = sum(len(i) for i, _ in lens)
    sup = sum(sum(x != -100 for x in lab) for _, lab in lens)
    print(json.dumps({"file": f, "n": len(recs), "total_tokens": tot, "mean": tot / len(recs), "supervised": sup}))
ids, lab = encode(tok, recs[0], model)
print(tok.decode(ids[-80:]))
print("SUPERVISED:", tok.decode([i for i, l in zip(ids, lab) if l != -100]))
