"""R1 synthetic data generator — Ramona (Laptop 2).

Generates factor-combinatorial appointment dialogue examples with
oracle labels derived from the actual BookingStore + Dialogue logic.
No LLM, no cloud API, no real PII.

Usage:
    python3 research/data_pipeline/generate_synthetic.py --out research/data/dev_set.jsonl --n 50

The generator:
1. Builds a Cartesian product over a declared factor subset
2. For each combination, instantiates a real Dialogue + BookingStore
3. Runs the utterance, records the actual action as the oracle label
4. Serialises to JSONL

This means oracle labels are provably correct (derived from code, not
human annotation) for the deterministic booking logic. Only intent
and slot extraction quality requires human/model evaluation.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue
from research.data_pipeline.schema import (
    DataExample, FactorTags, OracleLabel, SourceManifest,
)

# ---------------------------------------------------------------------------
# Seed utterances per factor combination
# ---------------------------------------------------------------------------

# (intent, time_precision, language_register) → list of utterance templates
# {DATE} = 2027-12-10, {TIME} = 14:00, {DUR} = 30|60|15
TEMPLATES: dict[tuple[str, str, str], list[str]] = {
    ("book", "exact", "formal"): [
        "book 2027-12-10 14:00",
        "book 2027-12-10 14:00 60",
        "book 2027-12-15 09:30 15",
        "book 2027-12-20 11:00 30",
    ],
    ("book", "exact", "casual"): [
        "book me in for 2027-12-10 at 14:00",
        "can you book 2027-12-10 14:00",
        "i want 2027-12-10 14:00",
    ],
    ("book", "exact", "typo"): [
        "bokk 2027-12-10 14:00",
        "book 2027-12-10 14:0",
        "book 2027--12-10 14:00",
    ],
    ("book", "dst_gap", "formal"): [
        "book 2027-03-14 02:30",
        "book 2027-03-14 02:00",
    ],
    ("book", "dst_fold", "formal"): [
        "book 2027-11-07 01:30",
        "book 2027-11-07 01:00",
    ],
    ("cancel", "absent", "formal"): [
        "cancel {REF}",
        "cancel 00000000-0000-0000-0000-000000000000",
    ],
    ("unknown", "absent", "casual"): [
        "hello",
        "what are your hours",
        "how much does it cost",
        "do you offer weekend appointments",
        "help",
    ],
    ("handoff", "absent", "formal"): [
        "i need to speak to a human",
        "can i talk to an operator",
        "get me a person",
    ],
    ("affirm", "absent", "formal"): [
        "yes",
        "confirm",
    ],
    ("deny", "absent", "formal"): [
        "no",
        "never mind",
        "cancel that",
    ],
}


def _make_id(utterance: str, session: str, seed: int) -> str:
    h = hashlib.sha1(f"{utterance}:{session}:{seed}".encode()).hexdigest()[:8]
    return f"syn-{h}"


def generate_examples(n: int | None = None) -> list[DataExample]:
    examples: list[DataExample] = []
    counter = 0

    for (intent, time_prec, lang), utterances in TEMPLATES.items():
        family_id = f"fam-{intent}-{time_prec}-{lang}"
        for i, utterance_template in enumerate(utterances):
            if n and counter >= n:
                break
            counter += 1
            seed = counter * 7 + 13
            session_id = f"syn-sess-{family_id}-{i}"

            # Resolve template placeholders
            # For cancel with real ref: use a fake UUID (not_found expected)
            utterance = utterance_template.replace(
                "{REF}", "00000000-0000-0000-0000-000000000000")

            # Run through real Dialogue to get oracle label
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
                db_path = f.name
            db = BookingStore(db_path)
            d = Dialogue(db, timezone_name="America/New_York")
            reply = d.handle(utterance, session_id)

            oracle = OracleLabel(
                expected_action=reply.action,
                expected_booking_created=(reply.action == "booked"),
                expected_db_active_count=len(db.active()),
                notes=f"oracle from Dialogue.handle: {reply.text[:60]}",
            )

            # Map (intent, time_prec, lang) to full FactorTags
            factor_tags = FactorTags(
                intent=intent if intent not in ("affirm", "deny") else "book",
                time_precision=time_prec,
                dialogue_turn="affirm" if intent == "affirm" else
                              "deny" if intent == "deny" else "clarification",
                calendar_state="free",
                language_register=lang,
            )

            ex = DataExample(
                example_id=_make_id(utterance, session_id, seed),
                family_id=family_id,
                source_id="project_synthetic",
                license="project-authored",
                factor_tags=factor_tags,
                utterance=utterance,
                session_id=session_id,
                reference_now=datetime.now(timezone.utc).isoformat(),
                timezone="America/New_York",
                oracle=oracle,
                seed=seed,
                human_reviewed=False,
                split="dev",
            )
            examples.append(ex)

        if n and counter >= n:
            break

    return examples


def to_jsonl(examples: list[DataExample]) -> str:
    lines = []
    for ex in examples:
        record = {
            "example_id": ex.example_id,
            "family_id": ex.family_id,
            "source_id": ex.source_id,
            "license": ex.license,
            "factors": {
                "intent": ex.factor_tags.intent,
                "time_precision": ex.factor_tags.time_precision,
                "dialogue_turn": ex.factor_tags.dialogue_turn,
                "calendar_state": ex.factor_tags.calendar_state,
                "language_register": ex.factor_tags.language_register,
            },
            "utterance": ex.utterance,
            "session_id": ex.session_id,
            "reference_now": ex.reference_now,
            "timezone": ex.timezone,
            "oracle": {
                "expected_action": ex.oracle.expected_action,
                "expected_booking_created": ex.oracle.expected_booking_created,
                "expected_db_active_count": ex.oracle.expected_db_active_count,
                "notes": ex.oracle.notes,
            },
            "seed": ex.seed,
            "human_reviewed": ex.human_reviewed,
            "split": ex.split,
        }
        lines.append(json.dumps(record))
    return "\n".join(lines)


def build_manifest() -> SourceManifest:
    return SourceManifest(
        source_id="project_synthetic",
        dataset_url="local:research/data_pipeline/generate_synthetic.py",
        revision="see git SHA",
        license="project-authored",
        commercial_use=True,
        redistribution_allowed=True,
        pii_risk="none",
        owner_approved=False,
        approval_date=None,
        notes="Pending schema audit per sources.csv policy. No personal data. "
              "Oracle labels from deterministic BookingStore + Dialogue code.",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="research/data/dev_set.jsonl")
    parser.add_argument("--n", type=int, default=None)
    args = parser.parse_args()

    examples = generate_examples(args.n)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(to_jsonl(examples))
    print(f"Written {len(examples)} examples to {args.out}")

    manifest = build_manifest()
    print(f"\nSource manifest (project_synthetic):")
    print(f"  owner_approved: {manifest.owner_approved}")
    print(f"  pii_risk: {manifest.pii_risk}")
    print(f"  NOTE: requires owner sign-off before training use")
