"""R2 near-duplicate detection — Ramona (Laptop 2).

Data hygiene / contamination tooling. The contamination literature (survey:
arxiv 2502.14425) recommends exact + near-duplicate + likelihood checks. The
diversity engine already drops exact-duplicate utterances and selects for
spread, so this module adds the *near*-duplicate layer: it flags surface-similar
pairs (cosine over the local n-gram embedder) within a dataset and ACROSS
datasets (e.g. training set vs. the gold eval anchor).

Deterministic, no model download, no network, no PII.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from research.data_pipeline.metrics import Embedder, default_embedder


def _get(ex, attr):
    return ex[attr] if isinstance(ex, dict) else getattr(ex, attr)


def _sim_matrix(texts, embedder: Embedder):
    x = embedder.embed(list(texts))
    return x @ x.T


def near_duplicate_pairs(examples, threshold: float = 0.92,
                         embedder: Embedder | None = None) -> list[dict]:
    """Return within-dataset pairs with cosine >= threshold, highest first."""
    if len(examples) < 2:
        return []
    embedder = embedder or default_embedder()
    s = _sim_matrix([_get(e, "utterance") for e in examples], embedder)
    n = len(examples)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if s[i, j] >= threshold:
                pairs.append({
                    "a_id": _get(examples[i], "id"),
                    "b_id": _get(examples[j], "id"),
                    "a": _get(examples[i], "utterance"),
                    "b": _get(examples[j], "utterance"),
                    "sim": round(float(s[i, j]), 4),
                })
    return sorted(pairs, key=lambda p: p["sim"], reverse=True)


def _clusters(n: int, pairs: list[tuple[int, int]]) -> list[list[int]]:
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in pairs:
        parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [g for g in groups.values() if len(g) > 1]


@dataclass
class DedupReport:
    n: int
    threshold: float
    near_dup_pairs: int
    duplicate_clusters: int
    examples_in_clusters: int
    effective_unique: int
    examples: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__


def dedup_report(examples, threshold: float = 0.92,
                 embedder: Embedder | None = None) -> DedupReport:
    embedder = embedder or default_embedder()
    pairs = near_duplicate_pairs(examples, threshold, embedder)
    id_to_idx = {_get(e, "id"): i for i, e in enumerate(examples)}
    idx_pairs = [(id_to_idx[p["a_id"]], id_to_idx[p["b_id"]]) for p in pairs]
    clusters = _clusters(len(examples), idx_pairs)
    in_clusters = sum(len(c) for c in clusters)
    # effective unique = total minus the redundant members of each cluster
    effective = len(examples) - sum(len(c) - 1 for c in clusters)
    return DedupReport(
        n=len(examples),
        threshold=threshold,
        near_dup_pairs=len(pairs),
        duplicate_clusters=len(clusters),
        examples_in_clusters=in_clusters,
        effective_unique=effective,
        examples=pairs[:20],
    )


def near_duplicates_across(a, b, threshold: float = 0.92,
                           embedder: Embedder | None = None) -> list[dict]:
    """Cross-dataset near-dups — e.g. training set vs. the gold eval anchor.
    A non-empty result is a surface-level contamination signal."""
    if not a or not b:
        return []
    embedder = embedder or default_embedder()
    xa = embedder.embed([_get(e, "utterance") for e in a])
    xb = embedder.embed([_get(e, "utterance") for e in b])
    s = xa @ xb.T
    out = []
    for i in range(len(a)):
        for j in range(len(b)):
            if s[i, j] >= threshold:
                out.append({
                    "a_id": _get(a[i], "id"), "b_id": _get(b[j], "id"),
                    "a": _get(a[i], "utterance"), "b": _get(b[j], "utterance"),
                    "sim": round(float(s[i, j]), 4),
                })
    return sorted(out, key=lambda p: p["sim"], reverse=True)
