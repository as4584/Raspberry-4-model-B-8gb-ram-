"""Fixed family-based split assignment, normalisation and near-duplicate measurement.

Families (intent:template) are assigned to train/dev/holdout by a FIXED rule that does not
depend on the seed, so no seed can ever put a holdout family into train or dev.
"""
from __future__ import annotations

import re

from lab.datagen.templates import TEMPLATES

SPLITS = ("train", "dev", "holdout")


def split_of_index(i: int) -> str:
    r = i % 7
    return "train" if r <= 4 else ("dev" if r == 5 else "holdout")


def families_for_split(split: str) -> list[str]:
    if split not in SPLITS:
        raise ValueError(split)
    return [f"{intent}:{i}" for intent, ts in TEMPLATES.items() for i in range(len(ts)) if split_of_index(i) == split]


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9:\- ]", " ", text.lower()).split())


def jaccard(a: str, b: str) -> float:
    sa, sb = set(normalize(a).split()), set(normalize(b).split())
    return len(sa & sb) / len(sa | sb) if sa | sb else 1.0


def near_duplicate_rate(texts: list[str], threshold: float = 0.9, sample: int = 400) -> float:
    """Share of the first `sample` texts having another text in the list with Jaccard >= threshold."""
    toks = [set(normalize(t).split()) for t in texts]
    hits = 0
    k = min(sample, len(toks))
    for i in range(k):
        for j in range(len(toks)):
            if i != j and toks[i] | toks[j] and len(toks[i] & toks[j]) / len(toks[i] | toks[j]) >= threshold:
                hits += 1
                break
    return hits / k if k else 0.0
