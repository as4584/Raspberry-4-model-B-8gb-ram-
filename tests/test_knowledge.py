"""Checked answers matched by meaning: a question the rules can't place gets the closest
vetted answer, or the free-call offer when nothing is close. Never a made-up fact."""
import json
import re
from pathlib import Path

import numpy as np
import pytest

from receptionist.booking import BookingStore
from receptionist.brain import Brain
from receptionist.knowledge import Knowledge
from tests.test_brain import CALLER, NOW, FakeIntents

ROOT = Path(__file__).resolve().parents[1]
VOCAB = {}


def bag(text):
    """Bag-of-words stand-in for the sentence encoder."""
    v = np.zeros(512)
    for w in re.findall(r"[a-z']+", text.lower()):
        v[VOCAB.setdefault(w, len(VOCAB) % 512)] += 1
    return v


def kb():
    return Knowledge.from_file(str(ROOT / "deploy" / "knowledge.json"), bag, threshold=0.5)


def test_every_entry_has_questions_and_a_spoken_answer():
    data = json.loads((ROOT / "deploy" / "knowledge.json").read_text(encoding="utf-8"))
    for e in data["entries"]:
        assert e["q"] and e["a"] and not re.search(r"[*#\[\]]|\d{3}", e["a"]), e["id"]


def test_closest_answer_wins():
    hit = kb().best("so are you a real person or what")
    assert hit and hit[0] == "real_person"


def test_nothing_close_returns_none():
    assert kb().best("purple elephant quantum soup") is None


@pytest.fixture
def brain(tmp_path):
    return Brain(BookingStore(str(tmp_path / "k.sqlite")), intents=FakeIntents(), clock=lambda: NOW, knowledge=kb())


def test_brain_uses_knowledge_instead_of_unknown(brain):
    r = brain.handle("Can you fix my laptop?", "c", caller_id=CALLER)
    assert r.action == "knowledge" and "repair" in r.speech


def test_brain_without_a_match_still_offers_the_free_call(brain):
    r = brain.handle("purple elephant quantum soup", "c", caller_id=CALLER)
    assert r.action == "unknown" and "free call" in r.speech


def test_mid_booking_reask_beats_knowledge(brain):
    brain.handle("Appointment tomorrow.", "c", caller_id=CALLER)
    assert brain.handle("tell me a joke", "c", caller_id=CALLER).action == "ask_again"
