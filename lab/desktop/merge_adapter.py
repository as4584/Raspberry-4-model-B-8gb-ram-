"""Desktop-only: merge a LoRA adapter into its base model and save safetensors + tokenizer for GGUF conversion."""
import argparse

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer

ap = argparse.ArgumentParser()
ap.add_argument("--model", required=True)
ap.add_argument("--revision", required=True)
ap.add_argument("--adapter", required=True)
ap.add_argument("--out", required=True)
a = ap.parse_args()
base = AutoModelForCausalLM.from_pretrained(a.model, revision=a.revision, torch_dtype=torch.bfloat16)
merged = PeftModel.from_pretrained(base, a.adapter).merge_and_unload()
merged.save_pretrained(a.out, safe_serialization=True)
AutoTokenizer.from_pretrained(a.model, revision=a.revision).save_pretrained(a.out)
print("merged ->", a.out)
