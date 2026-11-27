"""'This needs to close an appointment for the dumbest of users' (owner, 2026-11-27).
A caller who only ever says 'yeah', 'ok', 'sure' must end up booked, with no open questions."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


@pytest.mark.parametrize("yes", ["yeah", "ok", "sure", "yes", "sounds good", "yeah sure", "uh huh"])
def test_yes_only_caller_gets_booked(brain, yes):
    r = talk(brain, "What do you guys do?", "How much is it?", yes, yes)
    assert brain.store.active(), [said(x) for x in r]


def test_the_offer_names_real_times(brain):
    r = talk(brain, "What do you guys do?", "How much is it?")
    assert any(" a m" in said(x) or " p m" in said(x) for x in r)


def test_yeah_to_a_list_of_times_proposes_the_soonest(brain):
    r = talk(brain, "Can I book a call?", "yeah")
    assert r[1].action == "confirm_required"


def test_whatever_works_books_the_soonest(brain):
    r = talk(brain, "Can I book a call?", "whatever works for you", "yes")
    assert brain.store.active()


def test_no_still_means_no(brain):
    r = talk(brain, "What do you guys do?", "How much is it?", "no")
    assert not brain.store.active()
