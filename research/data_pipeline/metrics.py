"""R2 dataset quality metrics — Ramona (Laptop 2).

The diversity engine optimizes for *information density* and *coverage*, not
phrase count. This module provides the instruments used to measure that and to
drive diversity-maximizing selection:

  * Vendi Score — an embedding-based "effective number of distinct samples"
    (Friedman & Dieng, TMLR 2023, https://arxiv.org/abs/2210.02410). Higher =
    more diverse / less redundant. Computed with a pluggable local embedder.
  * Coverage — intent, slot, and compositional (factor-combination) coverage
    over the project taxonomy.
  * distinct-n — a cheap lexical-diversity complement.

Embedders are pluggable. The default `NgramEmbedder` has NO heavy dependencies
(numpy only) and is multilingual-robust via character n-grams, so it runs in CI
and on the Pi. If `sentence-transformers` is installed, `semantic_embedder()`
returns a multilingual sentence-embedding backend for true semantic diversity;
the Vendi math is identical, only the kernel improves.

No network, no API keys, no PII.
"""
from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Protocol, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Embedders
# ---------------------------------------------------------------------------


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> np.ndarray:
        """Return an (n, d) float array of L2-normalized row vectors."""
        ...


def _l2_normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class NgramEmbedder:
    """Dependency-free local embedder: hashed word + char n-gram term vectors.

    Deterministic (hashes via md5, not Python's salted hash). Captures
    lexical/surface diversity and, through char n-grams, works across English
    and Spanish without a language-specific tokenizer.
    """

    def __init__(
        self,
        dim: int = 1024,
        word_ns: tuple[int, ...] = (1, 2),
        char_ns: tuple[int, ...] = (3, 4),
    ) -> None:
        self.dim = dim
        self.word_ns = word_ns
        self.char_ns = char_ns

    def _features(self, text: str) -> Iterable[str]:
        low = text.lower().strip()
        words = low.split()
        for n in self.word_ns:
            for i in range(len(words) - n + 1):
                yield "w:" + " ".join(words[i : i + n])
        padded = f" {low} "
        for n in self.char_ns:
            for i in range(len(padded) - n + 1):
                yield "c:" + padded[i : i + n]

    def _bucket(self, feature: str) -> int:
        h = hashlib.md5(feature.encode("utf-8")).digest()
        return int.from_bytes(h[:4], "little") % self.dim

    def embed(self, texts: Sequence[str]) -> np.ndarray:
        mat = np.zeros((len(texts), self.dim), dtype=np.float64)
        for row, text in enumerate(texts):
            for feat in self._features(text):
                mat[row, self._bucket(feat)] += 1.0
        return _l2_normalize(mat)


def semantic_embedder(model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
    """Return a sentence-transformers backend if available, else None.

    Lets the Vendi Score upgrade from lexical to semantic diversity without
    changing any call sites, the moment the library is installed locally.
    """
    try:
        from sentence_transformers import SentenceTransformer
    except Exception:
        return None

    model = SentenceTransformer(model_name)

    class _ST:
        def embed(self, texts: Sequence[str]) -> np.ndarray:
            vecs = np.asarray(model.encode(list(texts), normalize_embeddings=True))
            return _l2_normalize(vecs.astype(np.float64))

    return _ST()


def default_embedder() -> Embedder:
    return semantic_embedder() or NgramEmbedder()


# ---------------------------------------------------------------------------
# Vendi Score
# ---------------------------------------------------------------------------


def vendi_score(
    texts: Sequence[str],
    embedder: Embedder | None = None,
    *,
    eps: float = 1e-12,
) -> float:
    """Effective number of distinct samples (q=1 / Shannon Vendi Score).

    1.0 = all items identical; up to n = all mutually orthogonal.
    """
    texts = list(texts)
    n = len(texts)
    if n == 0:
        return 0.0
    if n == 1:
        return 1.0
    embedder = embedder or default_embedder()
    x = embedder.embed(texts)                 # (n, d), rows L2-normalized
    k = (x @ x.T) / n                          # cosine kernel / n, trace == 1
    # symmetric PSD → real eigenvalues
    w = np.linalg.eigvalsh((k + k.T) / 2.0)
    w = w[w > eps]
    if w.size == 0:
        return 1.0
    w = w / w.sum()                            # guard numerical drift
    entropy = -float(np.sum(w * np.log(w)))
    return math.exp(entropy)


def distinct_n(texts: Sequence[str], n: int = 2) -> float:
    """Ratio of distinct n-grams to total n-grams (lexical diversity, 0..1)."""
    total = 0
    seen: set[tuple[str, ...]] = set()
    for t in texts:
        toks = t.lower().split()
        grams = [tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)]
        total += len(grams)
        seen.update(grams)
    return len(seen) / total if total else 0.0


# ---------------------------------------------------------------------------
# Coverage over the project taxonomy
# ---------------------------------------------------------------------------

# Canonical taxonomies this dataset is expected to span.
INTENTS = ["book", "affirm", "deny", "cancel", "handoff", "help",
           "unknown", "vague", "compound"]
LANGS = ["en", "es", "mixed"]
REGISTERS = ["formal", "casual", "keyword", "punct", "filler", "typo",
             "spelled", "relative", "vague", "compound", "asr", "code_switch"]

# Slot-value buckets a good booking dataset should exercise.
SLOT_BUCKETS = [
    "time_morning", "time_afternoon", "time_evening", "time_midnight",
    "time_on_hour", "time_half_hour", "time_quarter",
    "date_weekday", "date_weekend", "dst_edge",
    "duration_default", "duration_explicit", "reference_present",
]

DST_DATES = {"2027-03-14", "2027-11-07"}


def _slot_buckets(slot: str | None, utterance: str, intent: str) -> set[str]:
    """Derive which slot-value buckets an example exercises."""
    buckets: set[str] = set()
    low = utterance.lower()
    if intent == "cancel" or "-0000-" in utterance or "00000000-" in utterance:
        buckets.add("reference_present")
    if not slot:
        return buckets
    try:
        date, time_ = slot.split()
        hh, mm = (int(x) for x in time_.split(":"))
    except ValueError:
        return buckets
    if date in DST_DATES:
        buckets.add("dst_edge")
    # time-of-day
    if hh < 6 or hh == 0:
        buckets.add("time_midnight" if hh == 0 else "time_morning")
    if 6 <= hh < 12:
        buckets.add("time_morning")
    elif 12 <= hh < 17:
        buckets.add("time_afternoon")
    elif hh >= 17:
        buckets.add("time_evening")
    # minute granularity
    if mm == 0:
        buckets.add("time_on_hour")
    elif mm == 30:
        buckets.add("time_half_hour")
    elif mm in (15, 45):
        buckets.add("time_quarter")
    # weekday vs weekend
    try:
        import datetime as _dt
        wd = _dt.date.fromisoformat(date).weekday()
        buckets.add("date_weekend" if wd >= 5 else "date_weekday")
    except ValueError:
        pass
    # duration
    if len(slot.split()) >= 3:
        buckets.add("duration_explicit")
    else:
        buckets.add("duration_default")
    return buckets


@dataclass
class CoverageReport:
    n: int
    intent_coverage: float
    intent_distribution: dict
    lang_coverage: float
    lang_distribution: dict
    register_coverage: float
    slot_bucket_coverage: float
    slot_buckets_hit: dict
    compositional_coverage: float          # observed / possible (capped) combos
    compositional_cells: int               # distinct (intent, lang, register)
    missing_intents: list
    missing_slot_buckets: list

    def as_dict(self) -> dict:
        return self.__dict__


def coverage_report(examples: Sequence) -> CoverageReport:
    """`examples` are objects/dicts with: intent, lang, register, expected_slot,
    utterance."""
    def g(ex, attr):
        return ex[attr] if isinstance(ex, dict) else getattr(ex, attr)

    n = len(examples)
    intents = Counter(g(e, "intent") for e in examples)
    langs = Counter(g(e, "lang") for e in examples)
    registers = Counter(g(e, "register") for e in examples)

    slot_hits: Counter = Counter()
    for e in examples:
        for b in _slot_buckets(g(e, "expected_slot"), g(e, "utterance"),
                               g(e, "intent")):
            slot_hits[b] += 1

    # compositional: distinct (intent, lang, register) triples observed.
    triples = {(g(e, "intent"), g(e, "lang"), g(e, "register")) for e in examples}
    # "possible" is capped to a realistic envelope, not the raw cartesian
    # product (most cells are nonsensical, e.g. intent=handoff register=spelled).
    possible = min(len(INTENTS) * len(LANGS) * len(REGISTERS), 120)

    return CoverageReport(
        n=n,
        intent_coverage=len([i for i in INTENTS if intents.get(i)]) / len(INTENTS),
        intent_distribution=dict(intents),
        lang_coverage=len([l for l in LANGS if langs.get(l)]) / len(LANGS),
        lang_distribution=dict(langs),
        register_coverage=len([r for r in REGISTERS if registers.get(r)]) / len(REGISTERS),
        slot_bucket_coverage=len(slot_hits) / len(SLOT_BUCKETS),
        slot_buckets_hit=dict(slot_hits),
        compositional_coverage=min(1.0, len(triples) / possible),
        compositional_cells=len(triples),
        missing_intents=[i for i in INTENTS if not intents.get(i)],
        missing_slot_buckets=[b for b in SLOT_BUCKETS if b not in slot_hits],
    )
