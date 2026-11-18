"""Fail CI when the mutation score of the booking core drops below the floor.

    python scripts/mutation_gate.py mutants/mutmut-cicd-stats.json [--min 0.80]

Score = killed / (killed + survived + no_tests). Timeouts count as killed
(the mutant hung, which the suite detected). Raise MIN as tests improve; never
lower it to make a red run green.
"""
import argparse
import json
import sys
from pathlib import Path

MIN_SCORE = 0.80


def score(stats: dict) -> tuple[float, int, int]:
    killed = stats.get("killed", 0) + stats.get("timeout", 0)
    escaped = stats.get("survived", 0) + stats.get("no_tests", 0)
    total = killed + escaped
    return (killed / total if total else 0.0), killed, total


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("stats", type=Path)
    ap.add_argument("--min", type=float, default=MIN_SCORE)
    a = ap.parse_args(argv[1:])
    stats = json.loads(a.stats.read_text())
    s, killed, total = score(stats)
    print(f"mutation stats: {json.dumps(stats, sort_keys=True)}")
    print(f"MUTATION_SCORE={s:.3f} killed={killed}/{total} floor={a.min:.2f}")
    if total == 0:
        print("FAIL: no mutants were evaluated", file=sys.stderr)
        return 2
    if s < a.min:
        print("FAIL: mutation score below floor; run `mutmut results` locally and add tests", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
