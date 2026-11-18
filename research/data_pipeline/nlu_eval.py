"""R2 NLU evaluation dataset + harness — Ramona (Laptop 2).

A diverse, tiered, oracle-labeled benchmark for the NLU normalization layer.
The point of this file is RELIABILITY BEFORE PI: it lets us grade any NLU
implementation (today's rule-based `receptionist.nlu.normalize`, tomorrow's
local Qwen3 model on the Pi) against the same frozen set, off-device, in
milliseconds.

Design
------
Each example is labeled with:
  * expected_command  — the ideal `normalize()` output (structured command),
                        or None where we only grade behavior (passthrough).
  * expected_action   — the Dialogue action the full pipeline must produce.
  * expected_slot     — the "YYYY-MM-DD HH:MM" that MUST appear in the
                        normalized booking command (verifies 2pm -> 14:00 etc).

Tiers
-----
  Tier 1  canonical     rule-based MUST pass 100% (command + action)
  Tier 2  natural       rule-based MUST pass 100% (command + action)
  Tier 3  model-required rule-based is ALLOWED to miss the command, but MUST
                        fail safe: route to help/handoff, never mis-book.
                        This tier is the benchmark the Pi model must beat.

Safety invariant (ALL tiers, non-negotiable)
--------------------------------------------
  No example may produce a booking (confirm_required/booked) when none was
  warranted, and no booking may carry the wrong slot. A parser that cannot
  understand an utterance must fall through to help — never invent an
  appointment. The evaluator reports `unsafe` for any violation; the gate is 0.

No real PII, no audio, no network. Oracle actions come from the real
Dialogue + BookingStore, so labels are code-derived, not hand-waved.
"""
from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue

# Canonical fixtures used across examples.
_D = "2027-12-10"          # a safe future weekday, no DST edge
_REF = "00000000-0000-0000-0000-000000000000"   # fabricated cancel reference


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------

@dataclass
class NLUExample:
    id: str
    utterance: str
    lang: str         # en | es | mixed
    register: str     # formal|casual|keyword|filler|typo|punct|spelled|relative|vague|compound
    intent: str       # book|affirm|deny|cancel|handoff|help|unknown
    tier: int         # 1 | 2 | 3
    expected_action: str
    expected_command: str | None = None   # graded only when not None
    expected_slot: str | None = None      # "YYYY-MM-DD HH:MM" required in command
    notes: str = ""


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ExampleResult:
    id: str
    tier: int
    lang: str
    register: str
    intent: str
    produced_command: str
    produced_action: str
    command_graded: bool   # False when the example has no target command
    command_ok: bool
    action_ok: bool
    slot_ok: bool
    expected_action: str
    extracted_booking: bool  # produced confirm_required (did it parse a booking?)
    unsafe: bool
    reason: str = ""


# ---------------------------------------------------------------------------
# The gold set
# ---------------------------------------------------------------------------

def _booking_tier1() -> list[NLUExample]:
    out: list[NLUExample] = []
    # Structured passthrough (EN/ES both emit the same canonical command)
    out += [
        NLUExample("t1-book-struct-1", f"book {_D} 14:00", "en", "formal",
                   "book", 1, "confirm_required", f"book {_D} 14:00", f"{_D} 14:00"),
        NLUExample("t1-book-struct-dur", f"book {_D} 09:30 15", "en", "formal",
                   "book", 1, "confirm_required", f"book {_D} 09:30 15", f"{_D} 09:30"),
        NLUExample("t1-book-struct-60", f"book {_D} 11:00 60", "en", "formal",
                   "book", 1, "confirm_required", f"book {_D} 11:00 60", f"{_D} 11:00"),
        NLUExample("t1-book-es-reservar", f"reservar {_D} 14:00", "es", "formal",
                   "book", 1, "confirm_required", f"book {_D} 14:00", f"{_D} 14:00"),
        NLUExample("t1-book-es-agendar", f"agendar {_D} 09:00", "es", "formal",
                   "book", 1, "confirm_required", f"book {_D} 09:00", f"{_D} 09:00"),
        NLUExample("t1-book-es-programar", f"programar {_D} 11:00", "es", "formal",
                   "book", 1, "confirm_required", f"book {_D} 11:00", f"{_D} 11:00"),
    ]
    # 12-hour clock (EN + ES) — the slot check proves the conversion.
    clock = [
        (f"book {_D} at 2pm", "14:00", "en"),
        (f"book {_D} at 9am", "09:00", "en"),
        (f"book {_D} at 12pm", "12:00", "en"),
        (f"book {_D} at 12am", "00:00", "en"),
        (f"book {_D} at 11:45pm", "23:45", "en"),
        (f"reservar {_D} a las 2pm", "14:00", "es"),
        (f"reservar {_D} a las 9am", "09:00", "es"),
    ]
    for i, (utt, hhmm, lang) in enumerate(clock):
        out.append(NLUExample(
            f"t1-clock-{i}", utt, lang, "keyword", "book", 1,
            "confirm_required", f"book {_D} {hhmm}", f"{_D} {hhmm}",
            notes="12h->24h conversion"))
    return out


def _keyword_tier1() -> list[NLUExample]:
    affirm_en = ["yes", "yeah", "yep", "sure", "ok", "okay", "confirm",
                 "confirmed", "sounds good", "that works", "go ahead", "do it"]
    affirm_es = ["sí", "si", "de acuerdo", "claro", "perfecto", "adelante",
                 "confirmar", "está bien"]
    deny_en = ["no", "nope", "no thanks", "never mind", "cancel that", "forget it"]
    deny_es = ["no gracias", "cancelar eso", "olvídalo", "en realidad no"]
    human_en = ["operator", "I need a human", "agent please", "talk to someone"]
    human_es = ["operador", "necesito hablar con alguien", "agente por favor"]
    help_en = ["help", "what can you do", "how does this work"]
    help_es = ["ayuda", "cómo funciona", "necesito ayuda"]

    out: list[NLUExample] = []
    # Affirm keywords are graded with the deny-then-book flow around them, but
    # as a bare utterance "yes" with no pending booking => Dialogue "reply".
    for i, p in enumerate(affirm_en):
        out.append(NLUExample(f"t1-yes-en-{i}", p, "en", "keyword", "affirm", 1,
                              "reply", "yes", notes="affirm keyword (no pending)"))
    for i, p in enumerate(affirm_es):
        out.append(NLUExample(f"t1-yes-es-{i}", p, "es", "keyword", "affirm", 1,
                              "reply", "yes", notes="affirm keyword (no pending)"))
    for i, p in enumerate(deny_en):
        out.append(NLUExample(f"t1-no-en-{i}", p, "en", "keyword", "deny", 1,
                              "reply", "no", notes="deny keyword (no pending)"))
    for i, p in enumerate(deny_es):
        out.append(NLUExample(f"t1-no-es-{i}", p, "es", "keyword", "deny", 1,
                              "reply", "no", notes="deny keyword (no pending)"))
    for i, p in enumerate(human_en):
        out.append(NLUExample(f"t1-human-en-{i}", p, "en", "keyword", "handoff", 1,
                              "handoff", "human"))
    for i, p in enumerate(human_es):
        out.append(NLUExample(f"t1-human-es-{i}", p, "es", "keyword", "handoff", 1,
                              "handoff", "human"))
    for i, p in enumerate(help_en):
        out.append(NLUExample(f"t1-help-en-{i}", p, "en", "keyword", "help", 1,
                              "help", "help"))
    for i, p in enumerate(help_es):
        out.append(NLUExample(f"t1-help-es-{i}", p, "es", "keyword", "help", 1,
                              "help", "help"))
    # Cancel by fabricated reference (EN + ES) -> not_found, never a wrong delete.
    out.append(NLUExample("t1-cancel-en", f"cancel {_REF}", "en", "keyword",
                          "cancel", 1, "not_found", f"cancel {_REF}"))
    out.append(NLUExample("t1-cancel-es", f"cancelar {_REF}", "es", "keyword",
                          "cancel", 1, "not_found", f"cancel {_REF}"))
    return out


def _unknown_tier1() -> list[NLUExample]:
    # FAQ / out-of-scope: MUST route to help, never to a booking.
    faqs = [
        ("what are your hours", "en"),
        ("do you take insurance", "en"),
        ("how much does a cleaning cost", "en"),
        ("blah blah blah", "en"),
        ("¿cuánto cuesta?", "es"),
        ("¿están abiertos los sábados?", "es"),
        ("hola buenas tardes", "es"),
    ]
    return [
        NLUExample(f"t1-faq-{i}", utt, lang, "casual", "unknown", 1, "help",
                   expected_command=None, notes="out-of-scope -> help")
        for i, (utt, lang) in enumerate(faqs)
    ]


def _natural_tier2() -> list[NLUExample]:
    out: list[NLUExample] = []
    # English natural lead-ins
    en_nat = [
        (f"I'd like to book {_D} at 14:00", "14:00"),
        (f"Can you schedule me for {_D} 14:00", "14:00"),
        (f"Set up {_D} at 2pm", "14:00"),
        (f"sign me up for {_D} 14:00", "14:00"),
        (f"make it {_D} 14:00", "14:00"),
        (f"appointment on {_D} at 09:00", "09:00"),
        (f"book me in for {_D} at 2:30 pm", "14:30"),
    ]
    for i, (utt, hhmm) in enumerate(en_nat):
        out.append(NLUExample(f"t2-en-nat-{i}", utt, "en", "formal", "book", 2,
                              "confirm_required", f"book {_D} {hhmm}", f"{_D} {hhmm}"))
    # Spanish natural phrasings
    es_nat = [
        (f"quiero reservar el {_D} a las 14:00", "14:00"),
        (f"me gustaría una cita el {_D} a las 9am", "09:00"),
        (f"necesito una cita {_D} 15:30", "15:30"),
        (f"quisiera agendar para el {_D} a las 2pm", "14:00"),
    ]
    for i, (utt, hhmm) in enumerate(es_nat):
        out.append(NLUExample(f"t2-es-nat-{i}", utt, "es", "formal", "book", 2,
                              "confirm_required", f"book {_D} {hhmm}", f"{_D} {hhmm}"))
    # Punctuation / casing / trailing words on keywords
    punct_yes = ["yes please", "yes, please", "yep!", "sure thing", "okay!",
                 "sounds good!", "sí.", "sí, por favor", "claro que sí", "perfecto!"]
    for i, p in enumerate(punct_yes):
        lang = "es" if any(c in p for c in "í¡¿") or p.startswith(("sí", "claro", "perfecto")) else "en"
        out.append(NLUExample(f"t2-yes-punct-{i}", p, lang, "punct", "affirm", 2,
                              "reply", "yes"))
    punct_no = ["no, thank you", "nope!", "no thanks.", "no, gracias.", "nah"]
    for i, p in enumerate(punct_no):
        lang = "es" if "gracias" in p else "en"
        out.append(NLUExample(f"t2-no-punct-{i}", p, lang, "punct", "deny", 2,
                              "reply", "no"))
    # Casing + trailing punctuation on bookings
    out.append(NLUExample("t2-book-upper", f"BOOK {_D} 14:00", "en", "punct",
                          "book", 2, "confirm_required", None, f"{_D} 14:00",
                          notes="Dialogue lowercases; slot must survive"))
    out.append(NLUExample("t2-book-trail-dot", f"book {_D} at 2pm.", "en", "punct",
                          "book", 2, "confirm_required", f"book {_D} 14:00", f"{_D} 14:00"))
    out.append(NLUExample("t2-book-es-trail", f"reservar {_D} a las 2pm.", "es",
                          "punct", "book", 2, "confirm_required", f"book {_D} 14:00",
                          f"{_D} 14:00"))
    # Filler words wrapped around an explicit ISO date (still extractable)
    filler = [
        f"um yeah can I get {_D} at 2pm",
        f"so like, book {_D} 14:00 please",
        f"ok so I was thinking {_D} at 9am would work",
    ]
    for i, utt in enumerate(filler):
        out.append(NLUExample(f"t2-filler-{i}", utt, "en", "filler", "book", 2,
                              "confirm_required", None,
                              f"{_D} 14:00" if "2pm" in utt or "14:00" in utt else f"{_D} 09:00"))
    # Code-switching (mixed language in a single booking utterance)
    out.append(NLUExample("t2-mixed-es-en", f"quiero reservar {_D} at 2pm", "mixed",
                          "casual", "book", 2, "confirm_required", f"book {_D} 14:00",
                          f"{_D} 14:00"))
    out.append(NLUExample("t2-mixed-en-es", f"book {_D} a las 14:00", "mixed",
                          "casual", "book", 2, "confirm_required", f"book {_D} 14:00",
                          f"{_D} 14:00"))
    return out


def _dst_tier2() -> list[NLUExample]:
    # DST gap/fold must be rejected as invalid, never silently booked.
    return [
        NLUExample("t2-dst-gap", "book 2027-03-14 02:30", "en", "formal", "book",
                   2, "invalid", "book 2027-03-14 02:30", notes="spring-forward gap"),
        NLUExample("t2-dst-fold", "book 2027-11-07 01:30", "en", "formal", "book",
                   2, "invalid", "book 2027-11-07 01:30", notes="fall-back fold"),
        NLUExample("t2-dst-gap-es", "reservar 2027-03-14 02:30", "es", "formal",
                   "book", 2, "invalid", "book 2027-03-14 02:30"),
    ]


def _model_required_tier3() -> list[NLUExample]:
    # These carry the IDEAL label — what a perfect system (the Pi model) should
    # do. Rule-based has no ISO date to latch onto, so it will MISS the booking
    # and route to help. That is acceptable for Tier 3; the only hard gate is
    # that it FAILS SAFE (unsafe == 0 — never a wrong booking). Tier 3 is the
    # scored benchmark the local model must beat: raise booking extraction from
    # the rule-based 0% baseline while keeping unsafe at 0.
    #
    # (utterance, lang, register, intent, ideal_action)
    hard = [
        ("book tomorrow at 2pm", "en", "relative", "book", "confirm_required"),
        ("schedule me for next Monday at 10am", "en", "relative", "book", "confirm_required"),
        ("can I come in this Friday afternoon", "en", "relative", "book", "confirm_required"),
        ("book December tenth twenty twenty-seven at two pm", "en", "spelled", "book", "confirm_required"),
        ("the tenth of December at 2 in the afternoon", "en", "spelled", "book", "confirm_required"),
        ("sometime next week would be great", "en", "vague", "vague", "help"),
        ("whenever you have an opening", "en", "vague", "vague", "help"),
        ("cancel my 2pm and book 3pm instead", "en", "compound", "compound", "help"),
        ("quiero una cita mañana a las 2pm", "es", "relative", "book", "confirm_required"),
        ("resérvame el diez de diciembre a las dos de la tarde", "es", "spelled", "book", "confirm_required"),
        ("book december tenth two thousand twenty seven at two pee em", "en", "asr", "book", "confirm_required"),
        ("reservar diciembre diez a las dos", "es", "asr", "book", "confirm_required"),
    ]
    return [
        NLUExample(f"t3-{reg}-{i}", utt, lang, reg, intent, 3, action,
                   expected_command=None,
                   notes="model-required; rule-based must fail safe (no mis-book)")
        for i, (utt, lang, reg, intent, action) in enumerate(hard)
    ]


def gold_set() -> list[NLUExample]:
    examples: list[NLUExample] = []
    examples += _booking_tier1()
    examples += _keyword_tier1()
    examples += _unknown_tier1()
    examples += _natural_tier2()
    examples += _dst_tier2()
    examples += _model_required_tier3()
    # Integrity: ids must be unique.
    seen: set[str] = set()
    for ex in examples:
        if ex.id in seen:
            raise ValueError(f"duplicate example id: {ex.id}")
        seen.add(ex.id)
    return examples


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

_RECOGNIZED_BOOKING_ACTIONS = {"confirm_required", "booked"}


def evaluate_example(ex: NLUExample, normalize_fn) -> ExampleResult:
    command = normalize_fn(ex.utterance)

    with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
        db_path = f.name
    db = BookingStore(db_path)
    d = Dialogue(db, timezone_name="America/New_York")
    reply = d.handle(command, "eval")

    command_ok = ex.expected_command is None or command == ex.expected_command
    action_ok = reply.action == ex.expected_action

    slot_ok = True
    if ex.expected_slot is not None:
        # The slot must survive into the normalized booking command.
        slot_ok = ex.expected_slot in command

    # Safety: never invent a booking, never book the wrong slot.
    produced_booking = reply.action in _RECOGNIZED_BOOKING_ACTIONS
    should_book = ex.expected_action in _RECOGNIZED_BOOKING_ACTIONS
    unsafe = False
    reason = ""
    if produced_booking and not should_book:
        unsafe = True
        reason = f"hallucinated booking (expected {ex.expected_action})"
    elif produced_booking and should_book and ex.expected_slot and not slot_ok:
        unsafe = True
        reason = f"wrong slot: want {ex.expected_slot!r} in {command!r}"

    return ExampleResult(
        id=ex.id, tier=ex.tier, lang=ex.lang, register=ex.register,
        intent=ex.intent,
        produced_command=command, produced_action=reply.action,
        command_graded=ex.expected_command is not None,
        command_ok=command_ok, action_ok=action_ok, slot_ok=slot_ok,
        expected_action=ex.expected_action,
        extracted_booking=reply.action == "confirm_required",
        unsafe=unsafe, reason=reason,
    )


def evaluate(normalize_fn, examples: list[NLUExample] | None = None) -> dict:
    examples = examples or gold_set()
    results = [evaluate_example(ex, normalize_fn) for ex in examples]

    def _rate(subset, attr):
        subset = list(subset)
        if not subset:
            return None
        return sum(getattr(r, attr) for r in subset) / len(subset)

    by_tier = {}
    for tier in (1, 2, 3):
        rs = [r for r in results if r.tier == tier]
        graded = [r for r in rs if r.command_graded]
        # Extraction denominator = utterances that SHOULD become a booking.
        # DST-reject bookings (expected invalid) are excluded on purpose.
        book_intent = [r for r in rs
                       if r.intent == "book" and r.expected_action == "confirm_required"]
        by_tier[tier] = {
            "n": len(rs),
            # command accuracy only over examples that have a target command
            "command_acc": _rate(graded, "command_ok"),
            "command_graded_n": len(graded),
            "action_acc": _rate(rs, "action_ok"),
            "slot_acc": _rate(rs, "slot_ok"),
            # extraction = fraction of booking-intent utterances parsed to a
            # confirm. Tier 3 baseline is ~0 (no ISO date present); the Pi
            # model must raise this while keeping unsafe at 0.
            "extraction_rate": _rate(book_intent, "extracted_booking"),
            "book_intent_n": len(book_intent),
            "safe_rate": (1.0 - sum(r.unsafe for r in rs) / len(rs)) if rs else None,
            "unsafe": sum(r.unsafe for r in rs),
        }

    by_lang = {}
    for lang in ("en", "es", "mixed"):
        rs = [r for r in results if r.lang == lang]
        by_lang[lang] = {"n": len(rs), "action_acc": _rate(rs, "action_ok")}

    return {
        "total": len(results),
        "unsafe_total": sum(r.unsafe for r in results),
        "command_acc": _rate([r for r in results if r.command_graded], "command_ok"),
        "action_acc": _rate(results, "action_ok"),
        "by_tier": by_tier,
        "by_lang": by_lang,
        "failures": [asdict(r) for r in results
                     if not (r.command_ok and r.action_ok and r.slot_ok)],
        "unsafe_examples": [asdict(r) for r in results if r.unsafe],
    }


# ---------------------------------------------------------------------------
# Serialization + CLI
# ---------------------------------------------------------------------------

def to_jsonl(examples: list[NLUExample]) -> str:
    return "\n".join(json.dumps(asdict(ex), ensure_ascii=False) for ex in examples)


def _format_report(summary: dict) -> str:
    lines = ["NLU eval — rule-based baseline", "=" * 34,
             f"total examples : {summary['total']}",
             f"command acc    : {summary['command_acc']:.1%}",
             f"action acc     : {summary['action_acc']:.1%}",
             f"UNSAFE total   : {summary['unsafe_total']}  (gate = 0)",
             "", "by tier:"]
    for tier, m in summary["by_tier"].items():
        ca = f"{m['command_acc']:.0%}" if m["command_acc"] is not None else "n/a"
        aa = f"{m['action_acc']:.0%}" if m["action_acc"] is not None else "n/a"
        ex = f"{m['extraction_rate']:.0%}" if m["extraction_rate"] is not None else "n/a"
        lines.append(f"  tier {tier}: n={m['n']:<3} "
                     f"command={ca:<5}(graded {m['command_graded_n']}) "
                     f"action={aa:<5} book-extract={ex:<5} unsafe={m['unsafe']}")
    lines.append("")
    lines.append("by language:")
    for lang, m in summary["by_lang"].items():
        aa = f"{m['action_acc']:.0%}" if m["action_acc"] is not None else "n/a"
        lines.append(f"  {lang:<5}: n={m['n']:<3} action={aa}")
    if summary["failures"]:
        lines.append("")
        lines.append(f"failures ({len(summary['failures'])}):")
        for f in summary["failures"]:
            lines.append(f"  [{f['tier']}] {f['id']}: action={f['produced_action']} "
                         f"cmd={f['produced_command']!r}")
    return "\n".join(lines)


if __name__ == "__main__":
    from receptionist.nlu import normalize

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="research/data/nlu_eval_set.jsonl")
    parser.add_argument("--report", action="store_true",
                        help="print the rule-based baseline report")
    args = parser.parse_args()

    examples = gold_set()
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(to_jsonl(examples) + "\n", encoding="utf-8")
    print(f"Wrote {len(examples)} examples to {args.out}")

    if args.report:
        print()
        print(_format_report(evaluate(normalize)))
