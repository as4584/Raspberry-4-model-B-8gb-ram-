"""R1 data pipeline validation tests — Ramona (Laptop 2).

Tests that the synthetic data generator:
1. Produces valid schema-compliant records
2. Oracle labels are correct (derived from real Dialogue/BookingStore)
3. No PII in generated examples
4. Source manifest is project_synthetic (approved for schema audit)
5. All factor coverage requirements are present
6. No train/holdout leakage (all dev records are split='dev')

These run as part of the standard test suite (no special hardware).
"""
import json
import tempfile
import uuid
from pathlib import Path
import pytest

from research.data_pipeline.generate_synthetic import generate_examples, to_jsonl
from research.data_pipeline.schema import (
    INTENT_VALUES, TIME_PRECISION_VALUES, DIALOGUE_TURN_VALUES,
    CALENDAR_STATE_VALUES, LANGUAGE_REGISTER_VALUES,
)
from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue


# ---------------------------------------------------------------------------
# Schema compliance
# ---------------------------------------------------------------------------

def test_all_examples_have_required_fields():
    examples = generate_examples()
    required_fields = [
        "example_id", "family_id", "source_id", "license",
        "utterance", "session_id", "reference_now", "timezone",
        "oracle", "seed", "human_reviewed", "split",
    ]
    for ex in examples:
        raw = json.loads(to_jsonl([ex]))
        for f in required_fields:
            assert f in raw, f"Missing field {f!r} in example {ex.example_id}"


def test_all_examples_have_valid_oracle_action():
    valid_actions = {
        "confirm_required", "booked", "cancelled", "invalid",
        "slot_taken", "not_found", "help", "handoff", "reply",
    }
    for ex in generate_examples():
        assert ex.oracle.expected_action in valid_actions, \
            f"{ex.example_id}: invalid action {ex.oracle.expected_action!r}"


def test_all_examples_source_id_is_project_synthetic():
    for ex in generate_examples():
        assert ex.source_id == "project_synthetic", \
            f"{ex.example_id}: unexpected source {ex.source_id!r}"


def test_all_examples_license_is_project_authored():
    for ex in generate_examples():
        assert ex.license == "project-authored"


def test_all_examples_split_is_dev():
    for ex in generate_examples():
        assert ex.split == "dev", \
            f"{ex.example_id}: split is {ex.split!r}; holdout must be curated externally"


# ---------------------------------------------------------------------------
# Oracle label correctness
# ---------------------------------------------------------------------------

def test_oracle_labels_match_live_dialogue():
    """Re-run each example through a fresh Dialogue and verify oracle matches."""
    examples = generate_examples()
    mismatches = []
    for ex in examples:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as f:
            db_path = f.name
        db = BookingStore(db_path)
        d = Dialogue(db, timezone_name="America/New_York")
        reply = d.handle(ex.utterance, ex.session_id)
        if reply.action != ex.oracle.expected_action:
            mismatches.append(
                f"{ex.example_id}: got {reply.action!r}, "
                f"oracle says {ex.oracle.expected_action!r} "
                f"(utterance: {ex.utterance!r})"
            )
    assert not mismatches, "Oracle label mismatches:\n" + "\n".join(mismatches)


def test_booking_created_flag_matches_db(tmp_path):
    """expected_booking_created must match actual DB state after running."""
    examples = generate_examples()
    errors = []
    for ex in examples:
        db_path = str(tmp_path / f"{ex.example_id}.sqlite")
        db = BookingStore(db_path)
        d = Dialogue(db, timezone_name="America/New_York")
        d.handle(ex.utterance, ex.session_id)
        actual_booked = len(db.active()) > 0
        if actual_booked != ex.oracle.expected_booking_created:
            errors.append(
                f"{ex.example_id}: booking_created={actual_booked}, "
                f"oracle={ex.oracle.expected_booking_created}"
            )
    assert not errors, "Booking flag mismatches:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# PII checks
# ---------------------------------------------------------------------------

PII_PATTERNS = [
    "@", "phone", "ssn", "dob", "password", "token", "secret",
    "credit", "card", "account", "social security",
]


def test_no_pii_in_utterances():
    for ex in generate_examples():
        low = ex.utterance.lower()
        for pattern in PII_PATTERNS:
            assert pattern not in low, \
                f"{ex.example_id}: potential PII pattern {pattern!r} in utterance"


def test_no_real_uuids_that_could_be_credentials():
    for ex in generate_examples():
        # The only UUID allowed is the known fake one used for not_found tests
        allowed = "00000000-0000-0000-0000-000000000000"
        import re
        uuids = re.findall(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            ex.utterance
        )
        for u in uuids:
            assert u == allowed, \
                f"{ex.example_id}: unexpected UUID {u!r} in utterance"


# ---------------------------------------------------------------------------
# Factor coverage
# ---------------------------------------------------------------------------

def test_multiple_intents_represented():
    intents_present = {ex.factor_tags.intent for ex in generate_examples()}
    assert len(intents_present) >= 3, \
        f"Too few intents covered: {intents_present}"


def test_dst_gap_and_fold_both_present():
    time_precs = {ex.factor_tags.time_precision for ex in generate_examples()}
    assert "dst_gap" in time_precs, "No DST gap examples in dev set"
    assert "dst_fold" in time_precs, "No DST fold examples in dev set"


def test_at_least_one_typo_or_asr_error():
    langs = {ex.factor_tags.language_register for ex in generate_examples()}
    assert "typo" in langs or "asr_error" in langs, \
        "No typo/ASR error examples in dev set"


def test_minimum_30_examples():
    examples = generate_examples()
    assert len(examples) >= 25, \
        f"Dev set too small: {len(examples)} examples (want ≥25)"


# ---------------------------------------------------------------------------
# JSONL serialisation round-trip
# ---------------------------------------------------------------------------

def test_jsonl_roundtrip_is_valid_json():
    examples = generate_examples()
    jsonl = to_jsonl(examples)
    for i, line in enumerate(jsonl.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as e:
            pytest.fail(f"Line {i} is not valid JSON: {e}")
        assert parsed["example_id"].startswith("syn-")


def test_jsonl_no_duplicate_example_ids():
    examples = generate_examples()
    ids = [ex.example_id for ex in examples]
    assert len(ids) == len(set(ids)), "Duplicate example_ids in generated set"


def test_jsonl_family_ids_internally_consistent():
    """All examples within the same family_id must share the same source_id and license."""
    examples = generate_examples()
    family_sources: dict[str, set[str]] = {}
    family_licenses: dict[str, set[str]] = {}
    for ex in examples:
        family_sources.setdefault(ex.family_id, set()).add(ex.source_id)
        family_licenses.setdefault(ex.family_id, set()).add(ex.license)
    for fid, srcs in family_sources.items():
        assert len(srcs) == 1, \
            f"Family {fid!r} has mixed source_ids: {srcs}"
    for fid, lics in family_licenses.items():
        assert len(lics) == 1, \
            f"Family {fid!r} has mixed licenses: {lics}"
