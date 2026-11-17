"""R1 data pipeline schema — Ramona (Laptop 2).

Defines the factor taxonomy, example record structure, and manifest schema
for the S3A diverse synthetic dataset. No real PII, no audio.

All examples are project-authored (project_synthetic source_id) with
deterministic seeds and oracle labels from the BookingStore business logic.

Source approval status: project_synthetic — pending schema audit (sources.csv).
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# ---------------------------------------------------------------------------
# Factor taxonomy (from docs/data/coverage_matrix.md)
# ---------------------------------------------------------------------------

INTENT_VALUES = [
    "book", "availability_check", "cancel", "reschedule",
    "faq", "handoff", "unknown", "compound",
]

TIME_PRECISION_VALUES = [
    "exact", "vague", "relative", "absent",
    "dst_gap", "dst_fold", "across_midnight",
]

DIALOGUE_TURN_VALUES = [
    "affirm", "deny", "correction", "clarification",
    "repeated_confirm", "barge_in",
]

CALENDAR_STATE_VALUES = [
    "free", "overlap", "closing_hours", "outage", "concurrent_caller",
]

LANGUAGE_REGISTER_VALUES = [
    "casual", "formal", "code_switch_es_en", "filler_words",
    "typo", "asr_error",
]

# ---------------------------------------------------------------------------
# Example record
# ---------------------------------------------------------------------------

@dataclass
class FactorTags:
    intent: str
    time_precision: str
    dialogue_turn: str
    calendar_state: str
    language_register: str


@dataclass
class OracleLabel:
    expected_action: Literal[
        "confirm_required", "booked", "cancelled", "invalid",
        "slot_taken", "not_found", "help", "handoff", "reply",
    ]
    expected_booking_created: bool
    expected_db_active_count: int | None  # None = don't check
    notes: str = ""


@dataclass
class DataExample:
    example_id: str            # e.g. "syn-0001"
    family_id: str             # scenario family (grouped for split)
    source_id: str             # must be in sources.csv (approved)
    license: str               # inherited from source
    factor_tags: FactorTags
    utterance: str             # the text the caller says
    session_id: str            # for state machine continuity within family
    reference_now: str         # ISO8601 — "current time" for relative dates
    timezone: str              # IANA tz name
    oracle: OracleLabel
    seed: int                  # deterministic seed for any randomness
    human_reviewed: bool = False
    split: Literal["dev", "holdout"] = "dev"


# ---------------------------------------------------------------------------
# Manifest record (one per source used)
# ---------------------------------------------------------------------------

@dataclass
class SourceManifest:
    source_id: str
    dataset_url: str
    revision: str              # git SHA or version tag
    license: str
    commercial_use: bool
    redistribution_allowed: bool
    pii_risk: Literal["none", "low", "medium", "high"]
    owner_approved: bool
    approval_date: str | None  # ISO8601
    notes: str = ""
