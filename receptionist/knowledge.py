"""Checked answers (deploy/knowledge.json) matched by meaning with the brain's sentence encoder.
A question the rules can't place gets the closest vetted answer, or nothing when none is close:
the receptionist never makes up a fact. Plain Python (no numpy): ~150 rows x 384 dims is a few ms."""
from __future__ import annotations

import json
import math
from typing import Callable, Sequence


def _unit(v: Sequence[float]) -> list[float]:
    v = [float(x) for x in v]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n else v


class Knowledge:
    def __init__(self, entries: list[dict], embed: Callable[[str], Sequence[float]], threshold: float = 0.6):
        self._embed, self.threshold = embed, threshold
        self._ids: list[str] = []
        self._answers: dict[str, str] = {}
        self._rows: list[list[float]] = []
        for e in entries:
            self._answers[e["id"]] = e["a"]
            for q in e["q"]:
                self._ids.append(e["id"])
                self._rows.append(_unit(embed(q)))

    @classmethod
    def from_file(cls, path: str, embed, threshold: float = 0.6) -> "Knowledge":
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f)["entries"], embed, threshold)

    def answer(self, entry_id: str) -> str | None:
        return self._answers.get(entry_id)

    def best(self, text: str) -> tuple[str, str, float] | None:
        """(id, answer, similarity) of the closest checked answer, or None below the threshold."""
        if not self._rows:
            return None
        q = _unit(self._embed(text))
        sims = [sum(a * b for a, b in zip(r, q)) for r in self._rows]
        k = max(range(len(sims)), key=sims.__getitem__)
        if sims[k] < self.threshold:
            return None
        return self._ids[k], self._answers[self._ids[k]], sims[k]
