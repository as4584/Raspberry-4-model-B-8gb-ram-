"""Print Hugging Face Hub metadata (revision SHA, gating, licence, params) for registry provenance.

Usage: python lab/tools/hf_meta.py Qwen/Qwen3-0.6B [...]
Network-only helper; stdlib, no token used (gated repos report gated=...).
"""
import json
import sys
import urllib.request


def meta(model_id: str) -> dict:
    d = json.load(urllib.request.urlopen(f"https://huggingface.co/api/models/{model_id}", timeout=30))
    cd = d.get("cardData") or {}
    cfg = d.get("config") or {}
    return {
        "model_id": model_id,
        "sha": d.get("sha"),
        "gated": d.get("gated"),
        "license": cd.get("license"),
        "license_name": cd.get("license_name"),
        "license_link": cd.get("license_link"),
        "params": (d.get("safetensors") or {}).get("total"),
        "architectures": cfg.get("architectures"),
        "tools_in_chat_template": "tools" in json.dumps(cfg.get("tokenizer_config", {})),
    }


if __name__ == "__main__":
    for m in sys.argv[1:]:
        try:
            print(json.dumps(meta(m)))
        except Exception as e:  # noqa: BLE001 - report and continue
            print(json.dumps({"model_id": m, "error": str(e)}))
