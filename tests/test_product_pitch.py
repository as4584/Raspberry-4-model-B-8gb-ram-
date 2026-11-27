"""Round 4: every caller is a business owner she could sell herself to. Questions about the
receptionist product get the pitch, never the booking or cancel flows."""
import pytest

from tests.test_owner_facts import brain, kb  # noqa: F401  (fixture)
from tests.test_kindness import CALLER, said


@pytest.mark.parametrize("q", [
    "I'm a plumber, I'm under a sink half the day, can you answer my phone when I can't?",
    "Could a receptionist like you book appointments for my salon?",
    "What if a client cancels, can it fill the spot?",
    "Can you do this for my business?",
    "So could something like you pick up calls for my restaurant?",
])
def test_product_question_gets_the_pitch(brain, q):
    r = brain.handle(q, "c", caller_id=CALLER)
    assert r.action == "pitch" and "talking to one" in said(r)
    assert not brain.store.active()


def test_booking_for_themselves_is_still_booking(brain):
    r = brain.handle("Can you book me a call with Alex tomorrow?", "c", caller_id=CALLER)
    assert r.action != "pitch"


@pytest.mark.parametrize("q,entry", [
    ("Is your system HIPAA compliant?", "privacy"),
    ("Who owns the call recordings and the data?", "privacy"),
    ("Do you work with Square?", "integrations"),
    ("Can it connect to our scheduling software?", "integrations"),
    ("Do the leads get texted to my phone?", "lead_alerts"),
    ("Do I need to know computers to keep it up?", "maintenance"),
    ("My nephew does websites for three hundred, can you do seven hundred?", "haggle"),
    ("Can customers order online for pickup?", "online_ordering"),
    ("Is it just one guy? What if he gets hit by a bus?", "solo"),
    ("I want a website for my woodworking", "want_site"),
])
def test_round4_faq(q, entry):
    hit = kb().best(q)
    assert hit and hit[0] == entry, (q, hit)
