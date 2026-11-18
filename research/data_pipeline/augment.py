"""R2 augmentation providers — Ramona (Laptop 2).

Label-preserving surface realization for the diversity engine. A `Seed` carries
the ground-truth intent + canonical command + slot; providers emit `Variant`
surface forms. Nothing here trusts a provider blindly — the engine's critic
re-parses every variant and decides whether it preserves the label (see
diversity_engine.py). This is the "generate-then-filter" discipline from the
research (Impossible Distillation, NAACL 2024).

Two providers:
  * DeterministicAugmenter — rule-based realizers + label-preserving perturbers
    (EN/ES naturalizations, 12h clock, filler, punctuation, casing, connectors)
    plus parse-breaking tier-3 forms (spelled / relative / ASR). No deps.
  * LLMAugmenter — wraps an injected `generate_fn(prompt) -> list[str]`, so the
    LLM-in-loop stage is real but hermetic: tests inject a fake generator, and a
    local llama.cpp model can be wired in later. Outputs are untrusted until the
    critic validates them.

No network, no API keys, no PII.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# Seeds & variants
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Seed:
    intent: str                 # book|affirm|deny|handoff|help|cancel|unknown|vague|compound
    lang: str                   # en|es
    canonical: str              # the structured command (or the raw utterance for faq)
    slot: str | None            # "YYYY-MM-DD HH:MM" for bookings
    expected_action: str        # oracle action for a correct parse
    grade_command: str | None   # command to grade against, or None (ungraded)


@dataclass
class Variant:
    utterance: str
    register: str
    tier: int
    parse_preserving: bool      # provider's claim; the critic is the arbiter
    grades_command: bool        # whether normalize() should reproduce canonical exactly
    source: str                 # "det" | "llm"


# ---------------------------------------------------------------------------
# 12-hour clock helper
# ---------------------------------------------------------------------------

def to_12h(hhmm: str) -> str | None:
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    if m == 0:
        return f"{h12}{suffix}"
    return f"{h12}:{m:02d}{suffix}"


# ---------------------------------------------------------------------------
# Deterministic augmenter
# ---------------------------------------------------------------------------

_BOOK_LEAD_EN = [
    ("i'd like to book {D} at {T}", "formal", 2),
    ("can you schedule me for {D} {T}", "formal", 2),
    ("appointment on {D} at {T}", "casual", 2),
    ("sign me up for {D} {T}", "casual", 2),
    ("set up {D} at {T}", "casual", 2),
]
_BOOK_LEAD_ES = [
    ("quiero reservar el {D} a las {T}", "formal", 2),
    ("me gustaría una cita el {D} a las {T}", "formal", 2),
    ("necesito una cita {D} {T}", "casual", 2),
    ("agendar {D} {T}", "formal", 1),
]


class DeterministicAugmenter:
    source = "det"

    def variants(self, seed: Seed) -> list[Variant]:
        if seed.intent == "book":
            return self._book(seed)
        if seed.intent in ("affirm", "deny", "handoff", "help"):
            return self._keyword(seed)
        if seed.intent == "cancel":
            return [Variant(seed.canonical, "keyword", 1, True, True, self.source)]
        # vague / compound / unknown: a single surface form, graded only on action
        return [Variant(seed.canonical, seed.intent, 3 if seed.intent in
                        ("vague", "compound") else 1, False, False, self.source)]

    def _book(self, seed: Seed) -> list[Variant]:
        assert seed.slot
        date, t24 = seed.slot.split()
        t12 = to_12h(t24)
        out = [Variant(seed.canonical, "formal", 1, True, True, self.source)]

        # natural lead-ins (24h)
        leads = _BOOK_LEAD_EN if seed.lang == "en" else _BOOK_LEAD_ES
        for tmpl, reg, tier in leads:
            out.append(Variant(tmpl.format(D=date, T=t24), reg, tier, True, True,
                               self.source))
        # 12h clock variant
        if t12:
            kw = f"book {date} at {t12}" if seed.lang == "en" else f"reservar {date} a las {t12}"
            out.append(Variant(kw, "keyword", 1, True, True, self.source))

        # label-preserving perturbers on the canonical
        base = seed.canonical
        out += [
            Variant(base + ".", "punct", 2, True, True, self.source),
            Variant("um, so " + base, "filler", 2, True, True, self.source),
            Variant(base.upper(), "casing", 2, True, False, self.source),  # case lost
        ]
        # code-switch connector
        cs = (base.replace(f"{date} {t24}", f"{date} a las {t24}") if seed.lang == "en"
              else base.replace(f"{date} {t24}", f"{date} at {t24}"))
        out.append(Variant(cs, "code_switch", 2, True, True, self.source))

        # parse-breaking tier-3 (rule-based will miss; must fail safe). The
        # ISO date/time is destroyed, so these can only be solved by the model.
        verb = "book" if seed.lang == "en" else "reservar"
        if t12:
            rel = (f"book tomorrow at {t12}" if seed.lang == "en"
                   else f"quiero una cita mañana a las {t12}")
            out.append(Variant(rel, "relative", 3, False, False, self.source))
            spelled = (f"{verb} the tenth of december at {t12}" if seed.lang == "en"
                       else f"resérvame el diez de diciembre a las {t12}")
            out.append(Variant(spelled, "spelled", 3, False, False, self.source))
        # ASR-style: ISO date kept but the time is garbled into words
        out.append(Variant(f"{verb} {date} at two pee em", "asr", 3, False, False,
                           self.source))
        # typo that corrupts a digit in the ISO date
        out.append(Variant(base.replace("0", "o", 1), "typo", 3, False, False,
                           self.source))
        return out

    def _keyword(self, seed: Seed) -> list[Variant]:
        syn = _KEYWORD_SYNONYMS[(seed.intent, seed.lang)]
        out = []
        for i, phrase in enumerate(syn):
            out.append(Variant(phrase, "keyword", 1, True, True, self.source))
            if i == 0:  # add punctuation/casing variants on the primary form
                out.append(Variant(phrase + "!", "punct", 2, True, True, self.source))
                out.append(Variant(phrase.upper(), "casing", 2, True, True, self.source))
        return out


_KEYWORD_SYNONYMS = {
    ("affirm", "en"): ["yes", "yeah", "yep", "sure", "ok", "okay", "confirm",
                       "sounds good", "that works", "go ahead", "do it"],
    ("affirm", "es"): ["sí", "si", "de acuerdo", "claro", "perfecto", "adelante",
                       "confirmar", "está bien"],
    ("deny", "en"): ["no", "nope", "no thanks", "never mind", "cancel that",
                     "forget it"],
    ("deny", "es"): ["no gracias", "cancelar eso", "olvídalo", "en realidad no"],
    ("handoff", "en"): ["operator", "I need a human", "agent please",
                        "talk to someone"],
    ("handoff", "es"): ["operador", "necesito hablar con alguien",
                        "agente por favor"],
    ("help", "en"): ["help", "what can you do", "how does this work"],
    ("help", "es"): ["ayuda", "cómo funciona", "necesito ayuda"],
}


# ---------------------------------------------------------------------------
# LLM augmenter (pluggable, hermetic)
# ---------------------------------------------------------------------------

class LLMAugmenter:
    """Produces paraphrase candidates via an injected generate_fn.

    generate_fn(prompt: str) -> list[str]. The engine's critic validates every
    output, so a noisy or occasionally-wrong generator is safe: bad variants are
    dropped, label-preserving ones kept as tier 2, parse-breaking-but-safe ones
    kept as tier 3. None of them can introduce a mis-booking.
    """
    source = "llm"

    def __init__(self, generate_fn: Callable[[str], list[str]], max_per_seed: int = 4):
        self.generate_fn = generate_fn
        self.max_per_seed = max_per_seed

    def _prompt(self, seed: Seed) -> str:
        if seed.intent == "book" and seed.slot:
            return (
                "Paraphrase this appointment-booking request into natural "
                f"{'English' if seed.lang == 'en' else 'Spanish'} ways a caller "
                "might phrase it. KEEP the exact date and time unchanged "
                f"({seed.slot}). One paraphrase per line.\n"
                f"Request: {seed.canonical}"
            )
        return (
            f"Give short natural {'English' if seed.lang == 'en' else 'Spanish'} "
            f"phrasings for intent '{seed.intent}'. One per line."
        )

    def variants(self, seed: Seed) -> list[Variant]:
        try:
            raw = self.generate_fn(self._prompt(seed)) or []
        except Exception:
            return []
        out: list[Variant] = []
        for line in raw[: self.max_per_seed]:
            text = line.strip()
            if not text:
                continue
            # tier/grades are provisional; the critic re-classifies. We do not
            # claim exact command match for free-form paraphrase.
            out.append(Variant(text, "llm_paraphrase", 2, True, False, self.source))
        return out
