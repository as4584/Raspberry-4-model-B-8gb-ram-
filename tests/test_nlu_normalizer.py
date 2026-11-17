"""TDD RED — NLU command normalizer contract.

Defines expected normalization for English natural language and Spanish
phrases → structured commands the Dialogue layer understands.

Run before implementing receptionist/nlu.py to confirm RED state.
"""
import pytest
from receptionist.nlu import normalize


# ---------------------------------------------------------------------------
# English natural language → structured command
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance,expected", [
    # Explicit structured (passthrough)
    ("book 2027-12-10 14:00",          "book 2027-12-10 14:00"),
    ("book 2027-12-10 14:00 60",       "book 2027-12-10 14:00 60"),
    # Natural English booking phrases
    ("I'd like to book 2027-12-10 at 14:00",    "book 2027-12-10 14:00"),
    ("Can you schedule me for 2027-12-10 14:00", "book 2027-12-10 14:00"),
    ("Set up 2027-12-10 at 2pm",                "book 2027-12-10 14:00"),
    ("2027-12-10 at 3:30 pm",                   "book 2027-12-10 15:30"),
    ("appointment on 2027-12-10 at 09:00",      "book 2027-12-10 09:00"),
    ("make it 2027-12-10 14:00",                "book 2027-12-10 14:00"),
    ("sign me up for 2027-12-10 14:00",         "book 2027-12-10 14:00"),
    # Confirmation
    ("yes",             "yes"),
    ("yeah",            "yes"),
    ("yep",             "yes"),
    ("sure",            "yes"),
    ("sounds good",     "yes"),
    ("that works",      "yes"),
    ("confirm",         "yes"),
    ("confirmed",       "yes"),
    ("ok",              "yes"),
    ("okay",            "yes"),
    ("do it",           "yes"),
    ("go ahead",        "yes"),
    # Denial
    ("no",              "no"),
    ("nope",            "no"),
    ("no thanks",       "no"),
    ("cancel that",     "no"),
    ("never mind",      "no"),
    ("forget it",       "no"),
    ("actually no",     "no"),
    # Human handoff
    ("I need a human",          "human"),
    ("talk to someone",         "human"),
    ("let me speak to a person","human"),
    ("operator",                "human"),
    ("agent please",            "human"),
    # Help / FAQ
    ("help",                "help"),
    ("what can you do",     "help"),
    ("how does this work",  "help"),
])
def test_english_normalizer(utterance, expected):
    assert normalize(utterance) == expected


# ---------------------------------------------------------------------------
# Spanish natural language → structured command
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance,expected", [
    # Booking — various Spanish phrasings
    ("reservar 2027-12-10 14:00",               "book 2027-12-10 14:00"),
    ("quiero reservar el 2027-12-10 a las 14:00","book 2027-12-10 14:00"),
    ("hacer una cita 2027-12-10 14:00",         "book 2027-12-10 14:00"),
    ("agendar 2027-12-10 14:00",                "book 2027-12-10 14:00"),
    ("quisiera una cita el 2027-12-10 a las 9:00","book 2027-12-10 09:00"),
    ("me gustaría reservar 2027-12-10 a las 2pm","book 2027-12-10 14:00"),
    ("necesito una cita 2027-12-10 15:30",      "book 2027-12-10 15:30"),
    ("programar 2027-12-10 11:00",              "book 2027-12-10 11:00"),
    # Confirmation — Spanish
    ("sí",              "yes"),
    ("si",              "yes"),
    ("sí, confirmo",    "yes"),
    ("de acuerdo",      "yes"),
    ("está bien",       "yes"),
    ("claro",           "yes"),
    ("claro que sí",    "yes"),
    ("perfecto",        "yes"),
    ("adelante",        "yes"),
    ("confirmar",       "yes"),
    # Denial — Spanish
    ("no",              "no"),
    ("no gracias",      "no"),
    ("no, gracias",     "no"),
    ("cancelar eso",    "no"),
    ("olvídalo",        "no"),
    ("en realidad no",  "no"),
    # Human handoff — Spanish
    ("necesito hablar con alguien",   "human"),
    ("quiero hablar con un humano",   "human"),
    ("agente por favor",              "human"),
    ("operador",                      "human"),
    # Help — Spanish
    ("ayuda",           "help"),
    ("necesito ayuda",  "help"),
    ("cómo funciona",   "help"),
    # Cancel by reference — passes through
    ("cancel 00000000-0000-0000-0000-000000000000",
     "cancel 00000000-0000-0000-0000-000000000000"),
    ("cancelar 00000000-0000-0000-0000-000000000000",
     "cancel 00000000-0000-0000-0000-000000000000"),
])
def test_spanish_normalizer(utterance, expected):
    assert normalize(utterance) == expected


# ---------------------------------------------------------------------------
# 12-hour clock → 24-hour normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance,expected", [
    ("book 2027-12-10 at 2pm",      "book 2027-12-10 14:00"),
    ("book 2027-12-10 at 2:00pm",   "book 2027-12-10 14:00"),
    ("book 2027-12-10 at 2:30 pm",  "book 2027-12-10 14:30"),
    ("book 2027-12-10 at 12pm",     "book 2027-12-10 12:00"),
    ("book 2027-12-10 at 12am",     "book 2027-12-10 00:00"),
    ("book 2027-12-10 at 12:30am",  "book 2027-12-10 00:30"),
    ("book 2027-12-10 at 9am",      "book 2027-12-10 09:00"),
    ("book 2027-12-10 at 9:15 am",  "book 2027-12-10 09:15"),
    ("book 2027-12-10 at 11:45pm",  "book 2027-12-10 23:45"),
    ("reservar 2027-12-10 a las 2pm","book 2027-12-10 14:00"),
    ("reservar 2027-12-10 a las 9am","book 2027-12-10 09:00"),
])
def test_12h_clock_normalizer(utterance, expected):
    assert normalize(utterance) == expected


# ---------------------------------------------------------------------------
# Passthrough: unrecognized → returned unchanged (Dialogue handles fallback)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "what are your hours",
    "do you take insurance",
    "how long is a session",
    "¿cuánto cuesta?",
    "¿están abiertos los sábados?",
    "blah blah blah",
])
def test_unrecognized_passthrough(utterance):
    result = normalize(utterance)
    assert isinstance(result, str)
    assert len(result) > 0
