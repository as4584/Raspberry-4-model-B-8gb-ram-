"""Round 12: patience with a stutter, no false 'where'/'private' matches, deposits/intake/stock/galleries,
evening requests, names after booking, who's speaking in Spanish."""
import pytest

from tests.test_owner_facts import brain  # noqa: F401
from tests.test_kindness import CALLER, said
from receptionist.brain import collapse_repeats


def talk(brain, *turns):
    return [brain.handle(t, "c", caller_id=CALLER) for t in turns]


SLOTS = ("offer_slots", "confirm_required", "booked")


def test_stutter_collapsed():
    assert collapse_repeats("I-I-I w-w-want a w-website f-f-for my b-b-barbershop") == "I want a website for my barbershop"


def test_never_hang_up_on_an_on_topic_stutter(brain):
    r = talk(brain, "Mama W W website", "f-f-for my b-b-barbershop um um", "N-n-no, w-wait, w-website for my b-b-barbershop")
    assert not any(x.action == "hangup" for x in r)
    assert "website" in said(r[-1]) or "thousand" in said(r[-1])


def test_noise_still_ends(brain):
    r = talk(brain, "flurb", "glorp", "zib zab", "mmm blarg")
    assert any(x.action == "hangup" for x in r)


def test_where_in_a_request_is_not_our_location(brain):
    r = talk(brain, "I need someone to take the job, what car, what problem, and where they are")
    assert "paterson" not in said(r[0])


def test_private_galleries_are_a_website_feature(brain):
    r = talk(brain, "Can clients see their photos in private password protected galleries?")
    assert "gallerie" in said(r[0]) and "hipaa" not in said(r[0])


def test_storage_for_photos(brain):
    assert "storage" in said(talk(brain, "How much storage for thousands of high res photos?")[0])


def test_customer_deposits_through_cash_app(brain):
    r = talk(brain, "Can I take the deposit through Cash App or Square so they stop no showing?")
    assert "deposits" in said(r[0]) and "fifty percent" not in said(r[0])


def test_custom_intake_and_text(brain):
    r = talk(brain, "Can the robot ask what car, the problem, the address, and send me a text?")
    assert "questions you choose" in said(r[0]) or "ask each caller" in said(r[0])


def test_in_stock(brain):
    assert "stock" in said(talk(brain, "Can customers call and ask if something is in stock?")[0])


def test_evening_request_is_noted_not_ignored(brain):
    r = talk(brain, "Can I book a call Tuesday evening, like six or seven?")
    assert "evening" in said(r[0])


def test_name_from_a_long_answer_after_booking(brain):
    r = talk(brain, "Can I book something Friday at 10?", "Yes", "Dmitri. Job info: Honda Civic, brakes, in Clifton")
    assert "dmitri" in said(r[2])


def test_bye_girl_is_goodbye_not_a_name(brain):
    r = talk(brain, "Can I book something Friday at 11?", "Yes", "By girl")
    assert ", by" not in said(r[2])


def test_quien_habla(brain):
    r = talk(brain, "Aló, ¿quién habla?")
    assert "recepcionista" in said(r[0])


def test_spanish_stock(brain):
    r = talk(brain, "Aló, buenos días", "¿Cómo sabe si tengo leche o pan?")
    assert "productos" in said(r[1]) or "lista" in said(r[1])
