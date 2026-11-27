"""Round 5, second half: slower means simpler, money worries get kindness, a preferred callback
time is kept, 'are you legit' gets the honest answer, Spanish callers get Spanish."""
from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


def test_slower_is_shorter(brain):
    r = talk(brain, "How much is a website?", "Could you say that again, slower please?")
    assert len(said(r[1])) < len(said(r[0])) and "thousand" in said(r[1])


def test_fixed_income_gets_kindness(brain):
    r = talk(brain, "I'm on a fixed income, how much would it be?")
    assert "budget" in said(r[0]) or "understand" in said(r[0])


def test_preferred_time_goes_in_the_message(brain):
    talk(brain, "Can you take a message?", "Karen, 732 555 0148", "Have him call me back, best after 2 PM on weekdays")
    ms = brain.store.messages()
    assert ms and "after 2" in ms[0]["body"].lower()


def test_are_you_legit(brain):
    r = talk(brain, "How do I know you're legit? Got any examples?")
    assert "private" in said(r[0]) or "free call" in said(r[0])
    assert r[0].action != "unknown"


def test_spanish_caller_hears_spanish(brain):
    r = talk(brain, "Hola, bueno, mira, necesito una página web")
    assert "alex" in said(r[0]) and ("hola" in said(r[0]) or "llamar" in said(r[0]) or "número" in said(r[0]))
