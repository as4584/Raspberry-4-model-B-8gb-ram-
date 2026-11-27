"""Owner's answers (2026-11-26): deposit, ownership, cancelling, timeline, scope, no portfolio,
buying the receptionist. Each common phrasing must reach the owner's answer, not a guess."""
import pytest

from receptionist.booking import BookingStore
from receptionist.brain import Brain
from tests.test_brain import CALLER, NOW, FakeIntents
from tests.test_knowledge import kb

CASES = [
    ("Do you offer payment plans?", "fifty percent deposit"),
    ("Do you need a deposit up front?", "fifty percent deposit"),
    ("Do I own my domain?", "own your domain"),
    ("Is there a contract?", "no contract"),
    ("Do you give refunds?", "refunds"),
    ("What happens if I cancel?", "end of that month"),
    ("How long does it take to build a website?", "twenty four hours"),
    ("Do you build online stores?", "software and hardware"),
    ("Do you have a portfolio?", "private"),
    ("Can I buy this receptionist for my business?", "any business"),
]


@pytest.mark.parametrize("q,want", CASES)
def test_owner_fact_answered(q, want):
    hit = kb().best(q)
    assert hit, q
    import json
    from pathlib import Path
    entries = json.loads((Path(__file__).resolve().parents[1] / "deploy" / "knowledge.json").read_text(encoding="utf-8"))["entries"]
    a = next(e["a"] for e in entries if e["id"] == hit[0])
    assert want in a, (q, hit[0])


@pytest.fixture
def brain(tmp_path):
    from pathlib import Path
    from receptionist.profile import load_profile
    prof = load_profile(str(Path(__file__).resolve().parents[1] / "deploy" / "business_profile.json"))
    return Brain(BookingStore(str(tmp_path / "o.sqlite")), intents=FakeIntents(), clock=lambda: NOW, knowledge=kb(),
                 profile=prof)


def test_deposit_beats_one_time_or_monthly(brain):
    r = brain.handle("So do I have to pay everything at once or is there like a deposit?", "c", caller_id=CALLER)
    assert "deposit" in r.speech


def test_receptionist_price_is_the_sales_answer(brain):
    r = brain.handle("How much would it cost to get a receptionist like you for my salon?", "c", caller_id=CALLER)
    assert "any business" in r.speech.lower()


def test_stop_paying_reaches_cancel_answer():
    hit = kb().best("If I stop paying do I lose my website?")
    assert hit and hit[0] == "contract"
