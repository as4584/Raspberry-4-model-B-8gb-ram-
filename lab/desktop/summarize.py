"""Markdown table from run dirs containing scores.json (and train_meta.json for LoRA runs). Stdlib only."""
import json
import sys
from pathlib import Path

INTENTS = ["book", "availability", "reschedule", "cancel", "faq", "faq_unknown", "human", "emergency", "injection"]


def row(name, s):
    sf = s["safety"]
    c = s.get("config", {})
    by = s["by_intent"]
    per = " ".join(f"{i[:5]}={by[i]['exact']:.2f}" for i in INTENTS if i in by)
    return (f"| {name} | {s['n']} | {s['json_valid']:.3f} | {s['schema_valid']:.3f} | {s['exact']:.3f} | "
            f"{s['field_accuracy'].get('name', 0):.3f} | {sf['action_on_injection']} | {sf['false_action']} | "
            f"{sf['wrong_action_args']} | {sf['success_claim']} | {sf['total_violations']} | "
            f"{(c.get('prompt_sha256') or '')[:12]} | {per} |")


print("| run | n | json | schema | exact | tool | inj_action | false_action | wrong_args | claim | violations | prompt | exact by intent |")
print("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for d in sys.argv[1:]:
    p = Path(d)
    f = p / "scores.json" if (p / "scores.json").exists() else p / "eval_dev" / "scores.json"
    if f.exists():
        print(row(p.name, json.loads(f.read_text())))
