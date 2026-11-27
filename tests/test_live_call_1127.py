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


# Second live call (14:49): background noise in the owner's living room.
@pytest.mark.parametrize("frag", ["on", "That is that.", "Ha."])
def test_short_noise_fragment_gets_a_short_repeat(brain, frag):
    r = talk(brain, "Hi, what do you guys do?", frag)[1]
    assert len(said(r)) < 80 and "again" in said(r)


def test_noisy_turn_with_a_clear_question_at_the_end(brain):
    r = talk(brain, "Ha, I didn't have to go on low. No! And okay, start low killing you. Yeah, could you um... "
                    "Tell me a little bit more about how you can help my like Snicker reselling business?")
    assert r[0].action != "unknown" and "don't want to guess" not in said(r[0])


def test_how_can_you_help_my_business(brain):
    r = talk(brain, "How can you help my sneaker reselling business?")
    assert "website" in said(r[0]) or "talking to one" in said(r[0])


def test_leave_my_message(brain):
    r = talk(brain, "Oh yeah not should leave my message.")
    assert "name" in said(r[0])


def test_offers_the_next_step_after_a_couple_of_answers(brain):
    r = talk(brain, "What do you guys do?", "How long does it take to build a site?")
    assert any("follow up" in said(x) or "free call" in said(x) for x in r)


def test_asks_for_details_before_a_bare_goodbye(brain):
    r = talk(brain, "What do you guys do?", "Okay, thanks, bye")
    assert "name and number" in said(r[1]) and r[1].action != "goodbye"
    r2 = talk(brain, "No thanks, bye")
    assert r2[0].action == "goodbye"


def test_no_extra_ask_if_they_already_booked(brain):
    r = talk(brain, "Can I book Friday at 10 am?", "Yes", "Mike Delgado", "Yes", "Thanks, bye")
    assert r[-1].action == "goodbye"
