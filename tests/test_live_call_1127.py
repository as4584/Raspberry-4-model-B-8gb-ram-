"""Owner's live call 2026-11-27: 'Hello?' is 'are you there?', not 'how are you', and
'I never said anything' gets an apology, not 'I'm not sure I follow'."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


@pytest.mark.parametrize("hello", ["Hello?", "Hello", "Hello? Hello?", "Hi, hello?", "Are you there?"])
def test_hello_is_are_you_there(brain, hello):
    r = talk(brain, hello)[0]
    assert "doing well" not in said(r) and "here" in said(r)


@pytest.mark.parametrize("q", ["I never said anything, man.", "I didn't say that", "What are you talking about?"])
def test_i_never_said_that_gets_an_apology(brain, q):
    r = talk(brain, q)[0]
    assert "sorry" in said(r) and "not sure i follow" not in said(r)


def test_how_are_you_still_answered(brain):
    assert "doing well" in said(talk(brain, "Hi, how are you?")[0])
