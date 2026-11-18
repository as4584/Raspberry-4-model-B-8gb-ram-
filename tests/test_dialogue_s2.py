"""Sprint S2 booking safety (issue #8): business hours, reschedule via dialogue,
caller verification, DST through reschedule, idempotent replay through Dialogue."""
from datetime import time

import pytest

from receptionist.booking import BookingStore
import receptionist.dialogue as dialogue_mod
from receptionist.dialogue import Dialogue

CLOSED = "That time is outside our business hours. We are open Mon-Fri 09:00-17:00."


@pytest.fixture
def store(tmp_path):
    return BookingStore(str(tmp_path / "s2.sqlite"))


@pytest.fixture
def d(store):
    return Dialogue(store)


# ---- 1. business hours + service duration -------------------------------------------

def test_default_hours_are_weekdays_nine_to_five():
    assert dialogue_mod.DEFAULT_HOURS == {i: (time(9), time(17)) for i in range(5)}


@pytest.mark.parametrize("text", [
    "book 2027-12-11 14:00",        # Saturday
    "book 2027-12-12 10:00",        # Sunday
    "book 2027-12-10 08:59",        # before opening
    "book 2027-12-10 17:00",        # at closing
    "book 2027-12-10 16:45 30",     # runs past closing
    "book 2027-12-10 16:31 30",
])
def test_outside_hours_rejected_with_clear_reply(d, text):
    r = d.handle(text, "s")
    assert (r.action, r.text, r.committed_id) == ("closed", CLOSED, None)
    assert d.handle("yes", "s").text == "There is no pending booking to confirm."
    assert d.store.active() == []


@pytest.mark.parametrize("text", [
    "book 2027-12-10 09:00",
    "book 2027-12-10 16:30 30",     # ends exactly at closing
    "book 2027-12-10 16:00 60",
    "book 2027-12-10 16:45 15",
])
def test_inside_hours_boundaries_accepted(d, text):
    assert d.handle(text, "s").action == "confirm_required"
    assert d.handle("yes", "s").action == "booked"


def test_hours_are_local_time_not_utc(d):
    # 09:00 EST is 14:00Z; 08:00 EST is 13:00Z — local wall clock decides.
    assert d.handle("book 2027-01-20 09:00", "s").action == "confirm_required"
    assert d.handle("book 2027-01-20 08:00", "t").action == "closed"


def test_custom_hours_and_timezone(store):
    d = Dialogue(store, timezone_name="America/Los_Angeles",
                 hours={5: (time(10), time(14))})
    r = d.handle("book 2027-12-10 10:00", "s")
    assert r.action == "closed" and r.text == ("That time is outside our business hours. "
                                               "We are open Sat 10:00-14:00.")
    assert d.handle("book 2027-12-11 13:30", "s").action == "confirm_required"
    d.handle("yes", "s")
    (row,) = store.active()
    assert row["starts"] == "2027-12-11T21:30:00+00:00"   # 13:30 PST


def test_hours_text_groups_consecutive_days(store):
    d = Dialogue(store, hours={0: (time(8), time(12)), 1: (time(8), time(12)),
                               3: (time(9), time(17)), 6: (time(11), time(13))})
    assert d.handle("book 2027-12-11 10:00", "s").text == (
        "That time is outside our business hours. "
        "We are open Mon-Tue 08:00-12:00, Thu 09:00-17:00, Sun 11:00-13:00.")


def test_service_durations_configurable(store):
    d = Dialogue(store, durations=(20, 45), default_duration=45)
    r = d.handle("book 2027-12-10 10:00", "s")
    assert r.text == "Please confirm: Friday, December 10 at 10:00 AM for 45 minutes. Say yes or no."
    assert d.handle("book 2027-12-10 10:00 20", "s").text.endswith("for 20 minutes. Say yes or no.")
    r = d.handle("book 2027-12-10 10:00 30", "s")
    assert (r.action, r.text) == ("invalid", "Please choose a duration of 20 or 45 minutes.")
    assert d.handle("yes", "s").text == "There is no pending booking to confirm."


def test_default_durations_unchanged(d):
    r = d.handle("book 2027-12-10 10:00 45", "s")
    assert (r.action, r.text) == ("invalid", "Please choose a duration of 15, 30 or 60 minutes.")
