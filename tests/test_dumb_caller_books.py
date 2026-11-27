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


# Text-only low-effort panel (8 calls, 5 booked): every friction point.
@pytest.mark.parametrize("vague", ["yeah", "ok", "sure", "fine", "whatever", "i guess", "idk", "maybe", "i dunno"])
def test_vague_reply_after_her_question_moves_to_a_time(brain, vague):
    r = talk(brain, "What do you do?", vague)
    assert r[1].action in ("offer_slots", "confirm_required") or " a m" in said(r[1]) or " p m" in said(r[1]), said(r[1])


def test_no_to_a_slot_keeps_booking(brain):
    r = talk(brain, "I want to book a call", "no")
    assert r[1].action in ("offer_slots", "ask_day") and "anything else" not in said(r[1])


@pytest.mark.parametrize("more", ["what else you got", "other times", "whenever", "the times"])
def test_asking_for_other_times_keeps_booking(brain, more):
    r = talk(brain, "I want to book a call", more)
    assert r[1].action in ("offer_slots", "confirm_required", "ask_day"), said(r[1])


def test_bare_time_that_passed_today_moves_to_next_day(brain):
    r = talk(brain, "Can I book something?", "like 9")
    assert "passed" not in said(r[1]) or "tomorrow" in said(r[1]) or r[1].action in ("offer_slots", "confirm_required")


def test_what_were_you_saying_repeats(brain):
    r = talk(brain, "How much is a website?", "hold on my kid is yelling", "ok what were you saying")
    assert "thousand" in said(r[2])


@pytest.mark.parametrize("q", ["the call thing", "the free call", "i need a website", "hey i need a website"])
def test_easy_ways_in(brain, q):
    r = talk(brain, q)
    assert "not sure i follow" not in said(r[0]) and ("call" in said(r[0]) or " a m" in said(r[0]) or "website" in said(r[0]))


def test_lowercase_its_mike_mid_booking(brain):
    r = talk(brain, "book me i guess", "sure", "it's mike")
    assert "mike" in said(r[2])


def test_i_said_mike_at_the_name_prompt(brain):
    r = talk(brain, "Can I book Friday at 10 am?", "Yes", "i said mike")
    assert "mike" in said(r[2])


# Panel #2: 8/8 booked, but the after-booking state was broken.
@pytest.mark.parametrize("after", ["yeah", "ok", "sure"])
def test_after_booking_never_offers_slots_again(brain, after):
    talk(brain, "can i book something", "yeah", "yeah")
    assert len(brain.store.active()) == 1
    r = talk(brain, after, after)
    assert len(brain.store.active()) == 1
    assert all(x.action not in ("offer_slots", "confirm_required", "booked", "slot_taken") for x in r)


@pytest.mark.parametrize("nm", ["sam", "rob", "dan", "its dan"])
def test_short_lowercase_name_after_booking(brain, nm):
    talk(brain, "can i book something", "yeah", "yeah")
    r = talk(brain, nm)
    assert nm.split()[-1] in said(r[0])
    assert brain.store.active()[0]["name"].lower() == nm.split()[-1]


@pytest.mark.parametrize("q", ["hey i need a website", "um websites?", "website"])
def test_website_openers(brain, q):
    r = talk(brain, q)
    assert "website" in said(r[0]) and "not sure i follow" not in said(r[0]) and "catch that" not in said(r[0])
