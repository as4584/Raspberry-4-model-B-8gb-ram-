"""PI4 voice round trip 2026-11-18: the caller said "yes", whisper transcribed
"Yes.", the dialogue only matched bare "yes", fell through to help, and NO
booking was made. Confirmation words must survive ASR punctuation."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=ZoneInfo("America/New_York"))


@pytest.fixture
def d(tmp_path):
    return Dialogue(BookingStore(str(tmp_path / "p.sqlite")), clock=lambda: NOW)


@pytest.mark.parametrize("said", ["Yes.", "yes!", " Yes. ", "Confirm.", "YES"])
def test_punctuated_yes_confirms(d, said):
    d.handle("Book December 10 at 2 p.m.", "s")
    assert d.handle(said, "s").action == "booked"
    assert len(d.store.active()) == 1


@pytest.mark.parametrize("said", ["No.", "no!", "Never mind.", "Cancel that."])
def test_punctuated_no_declines(d, said):
    d.handle("book december tenth at two pm", "s")
    assert d.handle(said, "s").text == "Okay, no appointment was created."
    assert d.store.active() == []


def test_punctuated_cancel_reference_still_parses(d):
    d.handle("book december tenth at two pm", "s")
    ref = d.handle("yes", "s").committed_id
    assert d.handle(f"Cancel {ref}.", "s").action == "cancelled"


def test_yes_inside_a_sentence_is_not_a_confirmation(d):
    d.handle("book december tenth at two pm", "s")
    assert d.handle("yes but not that day", "s").action != "booked"
    assert d.store.active() == []
