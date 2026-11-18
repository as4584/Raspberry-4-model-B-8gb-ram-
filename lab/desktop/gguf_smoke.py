"""Desktop-only: run the first N dev prompts through a GGUF with llama-completion/llama-cli (CPU) and score them.

python lab/desktop/gguf_smoke.py <model.gguf> <dev.jsonl> <llama_bin_dir> [N]
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import torch  # noqa: E402  (only for its bundled libgomp path)
from transformers import AutoTokenizer  # noqa: E402

from lab.eval import prompt  # noqa: E402
from lab.eval.metrics import score  # noqa: E402

gguf, data, bindir = sys.argv[1:4]
n = int(sys.argv[4]) if len(sys.argv) > 4 else 40
tok = AutoTokenizer.from_pretrained("Qwen/Qwen2.5-0.5B-Instruct", revision="7ae557604adf67be50417f59c2c2f167def9a775")
recs = [json.loads(l) for l in Path(data).read_text(encoding="utf-8").splitlines()][:n]
exe = next(p for p in (Path(bindir).rglob("llama-completion")) if p.is_file()) if list(Path(bindir).rglob("llama-completion")) \
    else next(p for p in Path(bindir).rglob("llama-cli") if p.is_file())
env = dict(os.environ, LD_LIBRARY_PATH=f"{exe.parent}:{Path(torch.__file__).parent / 'lib'}")
pairs = []
for r in recs:
    text = tok.apply_chat_template(prompt.messages(r, layout=os.environ.get("PROMPT_LAYOUT", "v1")), tools=prompt.tool_specs(), add_generation_prompt=True, tokenize=False)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        f.write(text)
    out = subprocess.run([str(exe), "-m", gguf, "-f", f.name, "-n", "120", "--temp", "0", "-no-cnv", "-c", "4096",
                          "--no-display-prompt", "-t", "8"], capture_output=True, text=True, env=env, timeout=300).stdout
    pairs.append((r, out.split("<|im_end|>")[0]))
s = score(pairs)
print(json.dumps({"gguf": Path(gguf).name, "binary": exe.name, "n": s["n"], "schema_valid": s["schema_valid"],
                  "exact": s["exact"], "safety": s["safety"]}))
