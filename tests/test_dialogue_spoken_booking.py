"""S3: spoken booking requests (as whisper transcribes them) resolve through the
deterministic resolver into the SAME confirm -> book path; ambiguous requests
ask instead of guessing. Clock injected for determinism."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=NY)


@pytest.fixture
def d(tmp_path):
    return Dialogue(BookingStore(str(tmp_path / "s.sqlite")), clock=lambda: NOW)


@pytest.mark.parametrize("said", ["book december tenth at two pm", "Book December 10th at 2 p.m.", "book december 10 at 2pm"])
def test_spoken_booking_confirms_then_books(d, said):
    r = d.handle(said, "s")
    assert r.action == "confirm_required"
    assert r.text == "Please confirm: Thursday, December 10 at 02:00 PM for 30 minutes. Say yes or no."
    assert d.store.active() == []
    yes = d.handle("yes", "s")
    assert yes.action == "booked"
    (row,) = d.store.active()
    assert row["starts"] == "2026-12-10T19:00:00+00:00" and row["ends"] == "2026-12-10T19:30:00+00:00"


@pytest.mark.parametrize("said", ["book friday at two", "book december tenth", "book something nice", "book"])
def test_ambiguous_spoken_booking_asks(d, said):
    r = d.handle(said, "s")
    assert r.action == "clarify"
    assert r.text == "What day and time would you like? For example: December tenth at two p m."
    assert d.handle("yes", "s").text == "There is no pending booking to confirm."


def test_spoken_booking_outside_hours_is_refused(d):
    r = d.handle("book saturday at 2 pm", "s")
    assert r.action == "closed" and d.store.active() == []


def test_strict_format_still_works_with_clock(d):
    assert d.handle("book 2026-12-10 14:00", "s").action == "confirm_required"


def test_require_caller_id_applies_to_spoken_booking(tmp_path):
    d = Dialogue(BookingStore(str(tmp_path / "r.sqlite")), clock=lambda: NOW, require_caller_id=True)
    assert d.handle("book december tenth at two pm", "s").action == "verification_required"
    assert d.handle("book december tenth at two pm", "s", caller_id="+15551234567").action == "confirm_required"


def test_past_strict_time_uses_injected_clock(tmp_path):
    d = Dialogue(BookingStore(str(tmp_path / "p.sqlite")), clock=lambda: datetime(2027, 6, 1, tzinfo=NY))
    assert d.handle("book 2027-05-03 10:00", "s").text == "Please choose a future appointment time."
