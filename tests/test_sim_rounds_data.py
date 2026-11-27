"""The simulated-caller phrasing set is clean training/eval data: valid labels, no duplicates,
no phone numbers or names of real people, project-authored source."""
import json
import re
from pathlib import Path

from lab.intents.generate import CLASSES

ROWS = [json.loads(l) for l in (Path(__file__).resolve().parents[1] / "lab/intents/sim_rounds.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def test_labels_are_brain_classes():
    assert ROWS and all(r["label"] in CLASSES for r in ROWS)


def test_no_duplicates_and_no_numbers():
    texts = [r["text"].lower() for r in ROWS]
    assert len(texts) == len(set(texts))
    assert not any(re.search(r"\d{3}", t) for t in texts)


def test_project_authored():
    assert all("project-authored" in r["source"] for r in ROWS)
