"""Robotic moments from the owner's real calls (2026-11-23), as tests."""
import pytest

from tests.test_knowledge import CALLER, lexkb  # noqa: F401  (fixture)


def say(b, *turns):
    return [b.handle(t, "c", caller_id=CALLER) for t in turns]


def test_okay_then_a_question_answers_the_question(lexkb):
    r = say(lexkb, "Okay, like what available appointments do you guys have?")[0]
    assert r.action == "offer_slots" and " p m" in r.speech


@pytest.mark.parametrize("q", ["What available appointments do you have?", "What times do you have open?",
                               "When can I come in?"])
def test_availability_offers_times_instead_of_quizzing(lexkb, q):
    r = say(lexkb, q)[0]
    assert r.action == "offer_slots" and (" a m" in r.speech or " p m" in r.speech)


@pytest.mark.parametrize("noise", ["Um...", "Um... [pause]", "So...", "(grunting)", "You know, whoa.", "[BLANK_AUDIO]"])
def test_fillers_and_noise_get_a_patient_reply_not_a_dead_end(lexkb, noise):
    r = say(lexkb, noise)[0]
    assert r.action == "filler" and "outside" not in r.speech


def test_never_the_identical_sentence_twice_in_a_row(lexkb):
    a, b = say(lexkb, "What business do you work for?", "What business do you work for?")
    assert a.speech != b.speech


def test_identical_reask_is_rephrased(lexkb):
    say(lexkb, "Do you have any availability next week?")
    a, b = say(lexkb, "maybe because you're a girl", "you think I've known")
    assert a.speech != b.speech


@pytest.mark.parametrize("q", ["What business do you work for?", "Who do you work for?", "What company is this?"])
def test_who_do_you_work_for_introduces_itself(lexkb, q):
    s = say(lexkb, q)[0].speech
    assert "receptionist" in s and "LexMakesIt" in s


@pytest.mark.parametrize("q", ["Hello, are you there?", "Hello? Are you still there?", "Can you hear me?"])
def test_are_you_there_gets_a_reassuring_answer(lexkb, q):
    r = say(lexkb, q)[0]
    assert r.action == "presence" and "here" in r.speech.lower()


def test_yes_to_a_free_call_offer_starts_booking(lexkb):
    say(lexkb, "Do you guys do social media?")             # answer ends by offering a free call
    r = say(lexkb, "Yeah, sure, let's go over that.")[0]
    assert r.action == "offer_slots"


@pytest.mark.parametrize("q", ["I have a pressure washing business.", "I own a small bakery in Clifton.",
                               "I run a barbershop."])
def test_caller_describing_their_business_gets_a_tailored_pitch(lexkb, q):
    r = say(lexkb, q)[0]
    assert r.action == "pitch" and "free call" in r.speech


def test_repeated_offer_keeps_the_times(lexkb):
    a, b = say(lexkb, "What available appointments do you have?", "What available appointments do you have?")
    assert a.speech != b.speech and " a m" in b.speech
