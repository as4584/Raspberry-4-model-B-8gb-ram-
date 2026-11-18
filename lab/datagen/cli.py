"""CLI: python -m lab.datagen --split {train,dev,holdout} --seed N --n COUNT --out FILE [--mode diverse|narrow]

The sealed holdout is generated ONLY by the owner with a private seed, and may only be
written OUTSIDE this repository (refused otherwise) so it can never be committed or trained on.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from lab.datagen import coverage, generate, splits

REPO = Path(__file__).resolve().parents[2]


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="lab.datagen", description=__doc__)
    p.add_argument("--split", required=True, choices=splits.SPLITS)
    p.add_argument("--seed", required=True, type=int)
    p.add_argument("--n", required=True, type=int)
    p.add_argument("--out", required=True)
    p.add_argument("--mode", default="diverse", choices=["diverse", "narrow"])
    p.add_argument("--coverage-report", help="optional markdown coverage report path")
    return p.parse_args(argv)


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(REPO)
        return True
    except ValueError:
        return False


def main(argv=None) -> Path:
    a = parse_args(argv)
    out = Path(a.out)
    if a.split == "holdout" and _inside_repo(out):
        raise SystemExit("refusing: holdout output must be outside the repository (sealed, never committed)")
    records = generate.generate(a.split, a.seed, a.n, a.mode)
    out.parent.mkdir(parents=True, exist_ok=True)
    data = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in records)
    out.write_bytes(data.encode("utf-8"))  # exact bytes: no CRLF translation
    cov = coverage.pairwise(records)
    manifest = {
        "split": a.split, "seed": a.seed, "mode": a.mode, "count": len(records),
        "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest(),
        "families": sorted({r["family_id"] for r in records}),
        "pairwise_coverage": round(cov["coverage"], 4),
        "near_duplicate_rate_j90": round(splits.near_duplicate_rate([r["text"] for r in records]), 4),
        "generator_sha256": _generator_hash(),
    }
    Path(str(out) + ".manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if a.coverage_report:
        Path(a.coverage_report).write_text(coverage.report_markdown(records, f"Coverage {a.split}/{a.mode}/seed {a.seed}"),
                                           encoding="utf-8")
    print(json.dumps({k: v for k, v in manifest.items() if k != "families"}))
    return out


def _generator_hash() -> str:
    h = hashlib.sha256()
    for f in sorted((REPO / "lab" / "datagen").glob("*.py")):
        h.update(f.name.encode() + f.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()
