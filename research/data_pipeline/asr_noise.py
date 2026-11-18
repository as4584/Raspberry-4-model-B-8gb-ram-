"""R2 ASR-noise robustness — Ramona (Laptop 2).

Callers reach this system through ASR, so the NLU layer must be robust to
transcription noise: disfluencies, fillers, casing/punctuation drift, function-
word homophones, and garbled number words. Motivated by AR-NLU (Interspeech
2024) and interventional-noise SLU work, and implemented as CheckList-style
invariance/safety perturbations (Ribeiro et al., ACL 2020).

The one non-negotiable property this module checks:

    Under ANY ASR-style perturbation, the parser must NEVER book the WRONG
    slot. It may parse correctly, or it may fail safe (route to help / no
    booking) — but it must never confidently book a time the caller did not
    ask for.

(ISO-digit corruption that yields a *different valid date* is an upstream ASR
error, not a parser bug; the system mitigates it with the dialogue's read-back
confirmation step, not here. This module perturbs surface/phrasing, not the
ground-truth digits.)

Deterministic, no model, no network, no PII.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from research.data_pipeline.behavioral import _signature

# (utterance, intended_slot "YYYY-MM-DD HH:MM", lang)
_BOOKING_SEEDS = [
    ("book 2027-12-10 at 2pm", "2027-12-10 14:00", "en"),
    ("I'd like to book 2027-12-10 at 14:00", "2027-12-10 14:00", "en"),
    ("can you schedule me for 2028-01-05 09:00", "2028-01-05 09:00", "en"),
    ("reservar 2028-01-05 a las 9am", "2028-01-05 09:00", "es"),
    ("quiero reservar el 2027-12-20 a las 16:00", "2027-12-20 16:00", "es"),
    ("book 2027-12-11 at 11:15am", "2027-12-11 11:15", "en"),
]

# keyword utterances that must never turn into a booking under noise
_KEYWORD_SEEDS = ["yes", "no", "operator", "help", "sí", "no gracias", "ayuda"]


# ---------------------------------------------------------------------------
# Deterministic ASR-style perturbations
# ---------------------------------------------------------------------------

def _filler(s: str) -> str:
    parts = s.split()
    return " ".join(parts[:1] + ["um,"] + parts[1:]) if parts else s


def _disfluency(s: str) -> str:
    parts = s.split()
    return " ".join([parts[0]] + parts) if parts else s        # stutter first word


def _uppercase(s: str) -> str:
    return s.upper()


def _trailing_punct(s: str) -> str:
    return s + "?"


def _drop_connector(s: str) -> str:
    out = re.sub(r"\s+\bat\b\s+", " ", s)
    out = re.sub(r"\s+\ba las\b\s+", " ", out)
    return out


def _homophone(s: str) -> str:
    # common ASR function-word confusions; must not fabricate a booking
    subs = {r"\bno\b": "know", r"\byes\b": "yeah", r"\bfor\b": "four",
            r"\bto\b": "too"}
    out = s
    for pat, rep in subs.items():
        out = re.sub(pat, rep, out)
    return out


def _garble_time(s: str) -> str:
    # ASR mangles "2pm" -> "2 p.m." (dots break the am/pm token); or 24h minute
    out = re.sub(r"\b(\d{1,2})(am|pm)\b", r"\1 \2.", s)
    return out


# (name, fn, preserves_parse): preserves_parse=True perturbations SHOULD still
# resolve to the same slot; the rest just must fail safe.
_BOOKING_OPS = [
    ("filler", _filler, True),
    ("disfluency", _disfluency, True),
    ("uppercase", _uppercase, True),
    ("trailing_punct", _trailing_punct, True),
    ("drop_connector", _drop_connector, True),
    ("homophone", _homophone, True),
    ("garble_time", _garble_time, False),
]
_KEYWORD_OPS = [
    ("filler", _filler),
    ("uppercase", _uppercase),
    ("trailing_punct", _trailing_punct),
    ("homophone", _homophone),
]


@dataclass
class RobustnessReport:
    total: int = 0
    unsafe: int = 0                  # wrong-slot bookings — must be 0
    retained: int = 0                # still parsed to the correct slot
    failed_safe: int = 0             # didn't parse but routed to help/no-book
    keyword_total: int = 0
    keyword_unsafe: int = 0          # keyword turned into a booking — must be 0
    unsafe_examples: list = field(default_factory=list)

    @property
    def retention_rate(self) -> float:
        return self.retained / self.total if self.total else 0.0

    def as_dict(self) -> dict:
        return {
            "booking_perturbations": self.total,
            "unsafe": self.unsafe,
            "retained_correct": self.retained,
            "failed_safe": self.failed_safe,
            "retention_rate": round(self.retention_rate, 3),
            "keyword_perturbations": self.keyword_total,
            "keyword_unsafe": self.keyword_unsafe,
            "unsafe_examples": self.unsafe_examples,
        }


def run_robustness(normalize_fn) -> RobustnessReport:
    r = RobustnessReport()
    for utt, slot, _lang in _BOOKING_SEEDS:
        for name, fn, _preserves in _BOOKING_OPS:
            variant = fn(utt)
            r.total += 1
            action, got = _signature(normalize_fn, variant)
            if action in ("confirm_required", "booked"):
                if got == slot:
                    r.retained += 1
                else:
                    r.unsafe += 1
                    r.unsafe_examples.append(
                        f"[{name}] {variant!r} booked {got} want {slot}")
            else:
                r.failed_safe += 1
    for utt in _KEYWORD_SEEDS:
        for name, fn in _KEYWORD_OPS:
            variant = fn(utt)
            r.keyword_total += 1
            action, got = _signature(normalize_fn, variant)
            if action in ("confirm_required", "booked"):
                r.keyword_unsafe += 1
                r.unsafe_examples.append(
                    f"[kw/{name}] {variant!r} -> booking {got}")
    return r
