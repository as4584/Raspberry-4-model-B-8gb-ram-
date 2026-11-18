"""Synthetic receptionist data generator (lab/datagen): determinism, oracle labels, splits, leakage, coverage.

Never generates a holdout split: the sealed holdout is produced by the owner with a private seed.
"""
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from lab.datagen import generate, oracle, render, splits, coverage, cli
from lab.eval.schema import validate_call

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("America/New_York")
REF = datetime(2026, 11, 18, 10, 0, tzinfo=TZ)  # a Wednesday


# ---------- rendering ----------
@pytest.mark.parametrize("target,style,expected", [
    (date(2026, 11, 19), "tomorrow", "tomorrow"),
    (date(2026, 11, 20), "day_after_tomorrow", "the day after tomorrow"),
    (date(2026, 12, 3), "iso", "2026-12-03"),
    (date(2026, 12, 3), "month_day", "December 3rd"),
    (date(2026, 12, 3), "day_month", "the 3rd of December"),
    (date(2026, 11, 20), "weekday", "this Friday"),
    (date(2026, 11, 22), "in_n_days", "in 4 days"),
])
def test_date_phrases(target, style, expected):
    assert render.date_phrase(target, REF.date(), style) == expected


@pytest.mark.parametrize("hm,style,expected", [
    ("15:00", "h12", "3pm"),
    ("14:30", "h12min", "2:30 pm"),
    ("09:15", "h24", "09:15"),
    ("12:00", "words", "noon"),
    ("14:30", "words", "half past two in the afternoon"),
    ("10:15", "words", "quarter past ten in the morning"),
    ("16:00", "words", "four in the afternoon"),
])
def test_time_phrases(hm, style, expected):
    assert render.time_phrase(hm, style) == expected


def test_ordinals():
    assert [render.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 23)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd"]


# ---------- oracle policy ----------
def frame(**kw):
    base = dict(intent="book", date=date(2026, 11, 20), date_status="ok", time="15:00",
                time_status="ok", duration=30, reference=None, topic=None)
    base.update(kw)
    return base


def test_oracle_books_valid_slot():
    assert oracle.label(frame(), REF) == {"name": "book_appointment",
                                          "arguments": {"date": "2026-11-20", "time": "15:00", "duration_min": 30}}


@pytest.mark.parametrize("kw,missing", [
    (dict(date_status="missing", date=None), "date"),
    (dict(date_status="ambiguous"), "date"),
    (dict(date=date(2026, 11, 17)), "date"),           # past
    (dict(date=date(2026, 11, 22)), "date"),           # Sunday: closed
    (dict(time_status="missing", time=None), "time"),
    (dict(time_status="ambiguous"), "time"),
    (dict(time="08:00"), "time"),                      # before opening
    (dict(time="16:45", duration=30), "time"),         # ends after 17:00
    (dict(date=date(2027, 3, 13), time="02:30"), "time"),  # out of hours (DST checked separately)
])
def test_oracle_clarifies(kw, missing):
    out = oracle.label(frame(**kw), REF)
    assert out["name"] == "ask_clarification" and out["arguments"]["missing"] == missing


def test_local_time_status_detects_dst():
    assert oracle.local_time_status(date(2027, 3, 14), "02:30", TZ) == "nonexistent"
    assert oracle.local_time_status(date(2027, 11, 7), "01:30", TZ) == "ambiguous"
    assert oracle.local_time_status(date(2027, 3, 15), "10:00", TZ) == "ok"


def test_oracle_dst_never_books():
    out = oracle.label(frame(date=date(2027, 3, 14), time="02:30"), REF)
    assert out["name"] == "ask_clarification"


def test_oracle_same_day_past_time_clarifies():
    out = oracle.label(frame(date=date(2026, 11, 18), time="09:30"), REF)
    assert out["name"] == "ask_clarification" and out["arguments"]["missing"] == "time"


def test_oracle_reschedule_and_cancel_need_reference():
    ref = "3f2a1b4c-0000-4000-8000-00000000abcd"
    assert oracle.label(frame(intent="reschedule"), REF)["arguments"]["missing"] == "reference"
    assert oracle.label(frame(intent="cancel"), REF)["arguments"]["missing"] == "reference"
    assert oracle.label(frame(intent="cancel", reference=ref), REF) == {
        "name": "cancel_appointment", "arguments": {"reference": ref}}
    assert oracle.label(frame(intent="reschedule", reference=ref), REF)["name"] == "reschedule_appointment"


def test_oracle_availability_time_optional():
    out = oracle.label(frame(intent="availability", time=None, time_status="missing"), REF)
    assert out == {"name": "check_availability",
                   "arguments": {"date": "2026-11-20", "time": None, "duration_min": 30}}


@pytest.mark.parametrize("intent,topic,expected", [
    ("faq", "parking", {"name": "answer_faq", "arguments": {"topic": "parking"}}),
    ("faq_unknown", None, {"name": "handoff_to_human", "arguments": {"reason": "unknown_question"}}),
    ("human", None, {"name": "handoff_to_human", "arguments": {"reason": "caller_request"}}),
    ("emergency", None, {"name": "handoff_to_human", "arguments": {"reason": "emergency"}}),
    ("injection", None, {"name": "handoff_to_human", "arguments": {"reason": "unsafe_request"}}),
])
def test_oracle_non_slot_intents(intent, topic, expected):
    assert oracle.label(frame(intent=intent, topic=topic), REF) == expected


# ---------- generator ----------
@pytest.fixture(scope="module")
def train():
    return generate.generate("train", seed=7, n=600, mode="diverse")


@pytest.fixture(scope="module")
def dev():
    return generate.generate("dev", seed=7, n=200, mode="diverse")


def test_generator_is_deterministic(train):
    again = generate.generate("train", seed=7, n=600, mode="diverse")
    assert [json.dumps(r, sort_keys=True) for r in again] == [json.dumps(r, sort_keys=True) for r in train]
    other = generate.generate("train", seed=8, n=600, mode="diverse")
    assert [r["text"] for r in other] != [r["text"] for r in train]


def test_records_have_required_fields_and_valid_labels(train):
    req = {"id", "text", "reference_now", "timezone", "expected", "intent", "factors",
           "family_id", "split", "source_id", "license"}
    for r in train:
        assert req <= set(r), r
        assert r["split"] == "train"
        assert validate_call(r["expected"]) == [], r


def test_labels_match_oracle_recomputed(train):
    """The label is the oracle applied to the semantic frame, never derived from text."""
    for r in train:
        f = generate.frame_from_record(r)
        assert oracle.label(f, datetime.fromisoformat(r["reference_now"])) == r["expected"], r["id"]


def test_all_intents_and_safety_cases_present(train):
    intents = {r["intent"] for r in train}
    assert {"book", "availability", "reschedule", "cancel", "faq", "faq_unknown", "human",
            "emergency", "injection"} <= intents
    assert sum(r["intent"] == "injection" for r in train) >= 0.05 * len(train)


def test_injection_never_labelled_as_action(train):
    for r in train:
        if r["intent"] == "injection":
            assert r["expected"]["name"] == "handoff_to_human"


def test_no_exact_duplicates(train):
    norm = [splits.normalize(r["text"]) for r in train]
    assert len(norm) == len(set(norm))


def test_near_duplicate_detector():
    assert splits.jaccard("book me in tomorrow at 3pm please", "book me in tomorrow at 3pm") > 0.8
    assert splits.jaccard("cancel my appointment", "where do I park") < 0.2


def test_family_split_is_fixed_and_disjoint():
    fam = {s: set(splits.families_for_split(s)) for s in ("train", "dev", "holdout")}
    assert fam["train"] and fam["dev"] and fam["holdout"]
    assert not fam["train"] & fam["dev"]
    assert not fam["train"] & fam["holdout"]
    assert not fam["dev"] & fam["holdout"]
    # every intent has unseen families in dev and holdout
    for s in ("dev", "holdout"):
        assert {f.split(":")[0] for f in fam[s]} >= {f.split(":")[0] for f in fam["train"]}


def test_no_family_leakage_between_train_and_dev(train, dev):
    assert not {r["family_id"] for r in train} & {r["family_id"] for r in dev}
    assert {r["family_id"] for r in train} <= set(splits.families_for_split("train"))
    assert {r["family_id"] for r in dev} <= set(splits.families_for_split("dev"))
    assert not {splits.normalize(r["text"]) for r in train} & {splits.normalize(r["text"]) for r in dev}


def test_diverse_mode_pairwise_coverage_meets_gate(train):
    rep = coverage.pairwise(train)
    assert rep["coverage"] >= 0.95, rep["missing"][:20]


def test_narrow_mode_is_less_diverse():
    narrow = generate.generate("train", seed=7, n=600, mode="narrow")
    assert coverage.pairwise(narrow)["coverage"] < 0.5
    for r in narrow:
        assert validate_call(r["expected"]) == []


def test_factor_values_cover_required_phenomena(train):
    seen = {(k, v) for r in train for k, v in r["factors"].items()}
    for need in [("date_style", "tomorrow"), ("date_style", "weekday"), ("date_style", "next_weekday"),
                 ("correction", "date"), ("correction", "time"), ("noise", "asr"), ("noise", "fillers"),
                 ("register", "code_switch"), ("edge", "dst"), ("edge", "out_of_hours"), ("edge", "closed_day"),
                 ("time_style", "bare_ambiguous")]:
        assert need in seen, need


# ---------- CLI ----------
def test_cli_requires_explicit_seed():
    with pytest.raises(SystemExit):
        cli.parse_args(["--split", "holdout", "--n", "10", "--out", "x.jsonl"])


def test_cli_refuses_holdout_inside_repo(tmp_path):
    with pytest.raises(SystemExit) as e:
        cli.main(["--split", "holdout", "--seed", "1", "--n", "5", "--out", str(ROOT / "lab" / "out" / "h.jsonl")])
    assert "outside" in str(e.value)
    assert not (ROOT / "lab" / "out" / "h.jsonl").exists()


def test_cli_writes_train_and_manifest(tmp_path):
    out = tmp_path / "train.jsonl"
    cli.main(["--split", "train", "--seed", "3", "--n", "50", "--out", str(out)])
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 50
    man = json.loads((tmp_path / "train.jsonl.manifest.json").read_text())
    assert man["count"] == 50 and man["seed"] == 3 and man["split"] == "train"
    assert len(man["sha256"]) == 64 and man["families"]


def test_committed_sample_is_small_and_valid():
    sample = ROOT / "lab" / "datagen" / "sample.jsonl"
    lines = sample.read_text(encoding="utf-8").splitlines()
    assert 0 < len(lines) <= 20
    for line in lines:
        r = json.loads(line)
        assert r["split"] != "holdout" and validate_call(r["expected"]) == []


def test_generated_output_dir_is_gitignored():
    res = subprocess.run(["git", "check-ignore", "-q", "lab/out/x.jsonl"], cwd=ROOT)
    assert res.returncode == 0
