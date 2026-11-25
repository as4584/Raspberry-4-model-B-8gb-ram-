"""Checked answers matched by meaning: a question the rules can't place gets the closest
vetted answer, or the free-call offer when nothing is close. Never a made-up fact."""
import json
import re
from pathlib import Path

import pytest

from receptionist.booking import BookingStore
from receptionist.brain import Brain
from receptionist.knowledge import Knowledge
from tests.test_brain import CALLER, NOW, FakeIntents

ROOT = Path(__file__).resolve().parents[1]
VOCAB = {}


def bag(text):
    """Bag-of-words stand-in for the sentence encoder."""
    v = [0.0] * 512
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


def test_brain_without_a_match_asks_again_then_offers_the_free_call(brain):
    # Owner: dead ends are a setback. First miss: assume a noisy line and ask again.
    r = brain.handle("purple elephant quantum soup", "c", caller_id=CALLER)
    assert r.action == "unknown" and "loud where you are" in r.speech and "outside" not in r.speech
    r = brain.handle("green banana rocket stew", "c", caller_id=CALLER)
    assert r.action == "unknown" and "free call" in r.speech and "outside" not in r.speech


def test_mid_booking_reask_beats_knowledge(brain):
    brain.handle("Appointment tomorrow.", "c", caller_id=CALLER)
    assert brain.handle("tell me a joke", "c", caller_id=CALLER).action == "ask_again"


@pytest.fixture
def lexkb(tmp_path):
    from receptionist.profile import load_profile
    prof = load_profile(str(ROOT / "deploy" / "business_profile.json"))
    return Brain(BookingStore(str(tmp_path / "lk.sqlite")), intents=FakeIntents(), clock=lambda: NOW,
                 profile=prof, knowledge=kb())


def test_close_checked_answer_beats_a_keyword_hit(lexkb):
    s = lexkb.handle("How long does a website take?", "c", caller_id=CALLER).speech
    assert "timeline" in s and "five five one" not in s


def test_off_topic_is_declined_politely_not_sent_to_alex(lexkb):
    s = lexkb.handle("What is the capital of France?", "c", caller_id=CALLER).speech
    assert "outside what" not in s and "go over it" not in s and "loud" not in s


@pytest.mark.parametrize("q", ["Am I speaking with a real person right now?", "Are you a real person?", "Is this a human?"])
def test_asking_if_it_is_a_person_is_not_a_transfer_request(lexkb, q):
    r = lexkb.handle(q, "c", caller_id=CALLER)
    assert r.action != "handoff" and "receptionist" in r.speech


def test_transfer_request_has_a_real_next_step(lexkb):
    s = lexkb.handle("Can I talk to a real person?", "c", caller_id=CALLER).speech
    assert "five five one" in s and "callback" not in s


@pytest.mark.parametrize("q", ["Alright, that's everything, thank you.", "That's it for now, thanks.", "Nothing else, thanks."])
def test_closings_end_the_call_politely(lexkb, q):
    assert lexkb.handle(q, "c", caller_id=CALLER).action == "goodbye"


def test_when_are_you_free_asks_for_a_day(lexkb):
    r = lexkb.handle("I'd like to talk to someone about a website, when are you free?", "c", caller_id=CALLER)
    assert r.action in ("ask_day", "offer_slots")


@pytest.mark.parametrize("q", ["Can I just speak with Alex?", "Could I talk to the owner?", "Let me talk to somebody."])
def test_asking_to_talk_to_alex_or_a_person_gets_the_direct_line(lexkb, q):
    r = lexkb.handle(q, "c", caller_id=CALLER)
    assert r.action == "handoff" and "five five one" in r.speech


@pytest.mark.parametrize("q", ["Can you help me book an appointment?", "Could you help us set up an appointment?"])
def test_booking_request_beats_a_services_keyword(lexkb, q):
    assert lexkb.handle(q, "c", caller_id=CALLER).action in ("ask_day", "offer_slots")
