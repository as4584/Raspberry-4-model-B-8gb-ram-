"""Caller-facing dialogue contract, pinned after mutation testing showed these
replies, action codes and synonyms were executed but never asserted."""
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from receptionist.booking import BookingStore, KeyConflict
from receptionist.dialogue import Dialogue

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
T0 = datetime(2027, 1, 20, 14, tzinfo=timezone.utc)


@pytest.fixture
def d(tmp_path):
    return Dialogue(BookingStore(str(tmp_path / "d.sqlite")))


@pytest.mark.parametrize("text", ["I want a human", "operator please", "HUMAN"])
def test_handoff_requests(d, text):
    r = d.handle(text, "s")
    assert r.action == "handoff" and "human callback" in r.text and r.committed_id is None


def test_plain_text_is_not_handoff(d):
    r = d.handle("hello there", "s")
    assert r.action == "help" and "book 2026-12-01 14:00" in r.text


@pytest.mark.parametrize("decline", ["no", "never mind", "cancel that", "  No  "])
def test_decline_synonyms_clear_pending(d, decline):
    d.handle("book 2027-12-10 14:00", "s")
    r = d.handle(decline, "s")
    assert r.action == "reply" and "no appointment was created" in r.text
    assert d.handle("yes", "s").text == "There is no pending booking to confirm."
    assert d.store.active() == []


@pytest.mark.parametrize("accept", ["yes", "confirm", "YES"])
def test_accept_synonyms_book_on_primary_resource(d, accept):
    d.handle("book 2027-12-10 14:00", "s")
    r = d.handle(accept, "s")
    assert r.action == "booked" and UUID_RE.match(r.committed_id)
    assert r.text.startswith("Confirmed for 2027-12-10 02:00 PM.") and r.committed_id in r.text
    (row,) = d.store.active()
    assert row["resource"] == "primary" and row["id"] == r.committed_id


def test_confirm_prompt_wording_and_duration(d):
    r = d.handle("book 2027-12-10 14:00 60", "s")
    assert r.action == "confirm_required"
    assert r.text == "Please confirm: Friday, December 10 at 02:00 PM for 60 minutes. Say yes or no."
    d.handle("yes", "s")
    (row,) = d.store.active()
    assert row["ends"] == "2027-12-10T20:00:00+00:00"   # 15:00 EST + 60 min


def test_past_time_rejected(d):
    r = d.handle("book 2020-01-01 10:00", "s")
    assert r.text == "Please choose a future appointment time." and d.store.active() == []


def test_slot_taken_reply(d):
    other = Dialogue(d.store)
    d.handle("book 2027-12-10 14:00", "a")
    other.handle("book 2027-12-10 14:00", "b")
    assert d.handle("yes", "a").action == "booked"
    r = other.handle("yes", "b")
    assert r.action == "slot_taken" and r.text == "That time was just taken. Please choose another slot."


def test_store_conflict_becomes_safe_error_reply(d, monkeypatch):
    def boom(*a, **k):
        raise KeyConflict("x")
    monkeypatch.setattr(d.store, "book", boom)
    d.handle("book 2027-12-10 14:00", "s")
    r = d.handle("yes", "s")
    assert r.action == "error" and r.committed_id is None
    assert r.text == "I could not safely complete that appointment."


def test_cancel_replies(d):
    d.handle("book 2027-12-10 14:00", "s")
    ref = d.handle("yes", "s").committed_id
    r = d.handle(f"cancel {ref}", "s")
    assert (r.action, r.text) == ("cancelled", "The booking with that reference has been cancelled.")
    r = d.handle(f"cancel {ref}", "s")
    assert (r.action, r.text) == ("not_found", "I couldn't find an active appointment with that reference.")


def test_dst_messages(d):
    assert "Nonexistent" in d.handle("book 2027-03-14 02:30", "s").text or \
           "daylight saving" in d.handle("book 2027-03-14 02:30", "s").text
    assert "daylight saving" in d.handle("book 2027-11-07 01:30", "s").text


def test_reschedule_returns_fresh_uuid(tmp_path):
    store = BookingStore(str(tmp_path / "u.sqlite"))
    old = store.book("r", T0, T0 + timedelta(minutes=30), "k")
    new = store.reschedule(old["id"], T0 + timedelta(hours=1), T0 + timedelta(hours=1, minutes=30), "rs")
    assert UUID_RE.match(new["id"]) and uuid.UUID(new["id"]).version == 4 and new["id"] != old["id"]
    assert new["status"] == "active" and new["resource"] == "r"
    assert new["starts"] == "2027-01-20T15:00:00+00:00"


def test_booking_error_messages(tmp_path):
    from receptionist.booking import InvalidBooking, SlotTaken
    store = BookingStore(str(tmp_path / "m.sqlite"))
    x = store.book("r", T0, T0 + timedelta(minutes=30), "k")
    with pytest.raises(SlotTaken, match="^Requested slot is already taken$"):
        store.book("r", T0, T0 + timedelta(minutes=30), "k2")
    with pytest.raises(KeyConflict, match="^Idempotency key reused with different slot$"):
        store.book("r", T0 + timedelta(hours=3), T0 + timedelta(hours=4), "k")
    store.cancel(x["id"])
    with pytest.raises(InvalidBooking, match="^No active booking with that reference$"):
        store.reschedule(x["id"], T0, T0 + timedelta(minutes=30), "rs")
