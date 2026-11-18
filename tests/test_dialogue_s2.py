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


# ---- 2. reschedule through Dialogue -------------------------------------------------

def _book(d, text="book 2027-12-10 10:00", session="s", **kw):
    d.handle(text, session, **kw)
    r = d.handle("yes", session, **kw)
    assert r.action == "booked"
    return r.committed_id


def test_reschedule_requires_confirmation_then_moves(d, store):
    ref = _book(d)
    r = d.handle(f"reschedule {ref} 2027-12-13 11:00", "s")
    assert r.action == "confirm_required" and r.committed_id is None
    assert r.text == "Please confirm moving your appointment to Monday, December 13 at 11:00 AM for 30 minutes. Say yes or no."
    (row,) = store.active()
    assert row["id"] == ref                       # nothing moved before yes
    r = d.handle("yes", "s")
    assert r.action == "rescheduled" and r.committed_id not in (None, ref)
    assert r.text == f"Rescheduled to 2027-12-13 11:00 AM. Your new reference is {r.committed_id}."
    (row,) = store.active()
    assert (row["id"], row["starts"], row["ends"]) == (
        r.committed_id, "2027-12-13T16:00:00+00:00", "2027-12-13T16:30:00+00:00")


def test_reschedule_with_duration(d, store):
    ref = _book(d)
    assert d.handle(f"reschedule {ref} 2027-12-13 11:00 60", "s").text.endswith("for 60 minutes. Say yes or no.")
    d.handle("yes", "s")
    (row,) = store.active()
    assert row["ends"] == "2027-12-13T17:00:00+00:00"


def test_reschedule_declined_keeps_original(d, store):
    ref = _book(d)
    d.handle(f"reschedule {ref} 2027-12-13 11:00", "s")
    r = d.handle("no", "s")
    assert r.text == "Okay, your appointment was not changed."
    assert d.handle("yes", "s").text == "There is no pending booking to confirm."
    (row,) = store.active()
    assert row["id"] == ref


def test_reschedule_pending_is_per_session(d, store):
    ref = _book(d)
    d.handle(f"reschedule {ref} 2027-12-13 11:00", "s")
    assert d.handle("yes", "other").text == "There is no pending booking to confirm."
    assert d.handle("yes", "s").action == "rescheduled"


def test_reschedule_outside_hours_rejected(d, store):
    ref = _book(d)
    r = d.handle(f"reschedule {ref} 2027-12-11 11:00", "s")
    assert (r.action, r.text) == ("closed", CLOSED)
    assert d.handle("yes", "s").text == "There is no pending booking to confirm."


def test_reschedule_past_and_bad_duration_rejected(d):
    ref = _book(d)
    assert d.handle(f"reschedule {ref} 2020-01-06 10:00", "s").text == "Please choose a future appointment time."
    assert d.handle(f"reschedule {ref} 2027-12-13 10:00 45", "s").action == "invalid"
    assert d.handle("yes", "s").text == "There is no pending booking to confirm."


def test_reschedule_unknown_reference(d):
    d.handle("reschedule 00000000-0000-4000-8000-000000000000 2027-12-13 11:00", "s")
    r = d.handle("yes", "s")
    assert (r.action, r.text, r.committed_id) == (
        "not_found", "I couldn't find an active appointment with that reference.", None)


def test_reschedule_into_taken_slot(d, store):
    ref = _book(d)
    _book(d, "book 2027-12-13 11:00", session="x")
    d.handle(f"reschedule {ref} 2027-12-13 11:00", "s")
    r = d.handle("yes", "s")
    assert (r.action, r.text) == ("slot_taken", "That time was just taken. Please choose another slot.")
    assert ref in [b["id"] for b in store.active()]


def test_reschedule_then_cancel_new_reference(d, store):
    ref = _book(d)
    d.handle(f"reschedule {ref} 2027-12-13 11:00", "s")
    new = d.handle("yes", "s").committed_id
    assert d.handle(f"cancel {ref}", "s").action == "not_found"
    assert d.handle(f"cancel {new}", "s").action == "cancelled"
    assert store.active() == []


# ---- 3. caller verification for cancel / reschedule ---------------------------------
import sqlite3
from datetime import datetime, timedelta, timezone

from receptionist.booking import InvalidBooking

NOT_FOUND = ("not_found", "I couldn't find an active appointment with that reference.")
ALICE, MALLORY = "+15550000001", "+15550000002"
T1 = datetime(2027, 12, 10, 15, tzinfo=timezone.utc)


def test_store_records_caller_id(store):
    row = store.book("r", T1, T1 + timedelta(minutes=30), "k", caller_id=ALICE)
    assert row["caller_id"] == ALICE
    assert store.book("r", T1 + timedelta(hours=1), T1 + timedelta(hours=2), "k2")["caller_id"] is None


def test_store_cancel_requires_same_caller(store):
    ref = store.book("r", T1, T1 + timedelta(minutes=30), "k", caller_id=ALICE)["id"]
    assert store.cancel(ref, caller_id=MALLORY) is False
    assert store.cancel(ref) is False
    assert [b["id"] for b in store.active()] == [ref]
    assert store.cancel(ref, caller_id=ALICE) is True
    assert store.active() == []


def test_store_reschedule_requires_same_caller_and_keeps_owner(store):
    ref = store.book("r", T1, T1 + timedelta(minutes=30), "k", caller_id=ALICE)["id"]
    later = T1 + timedelta(hours=1)
    for who in (MALLORY, None):
        with pytest.raises(InvalidBooking, match="^No active booking with that reference$"):
            store.reschedule(ref, later, later + timedelta(minutes=30), f"rs-{who}", caller_id=who)
    assert [b["id"] for b in store.active()] == [ref]
    new = store.reschedule(ref, later, later + timedelta(minutes=30), "rs", caller_id=ALICE)
    assert new["caller_id"] == ALICE
    assert store.cancel(new["id"], caller_id=ALICE) is True


def test_dialogue_cancel_by_other_caller_is_indistinguishable_from_unknown(d, store):
    ref = _book(d, caller_id=ALICE)
    assert store.active()[0]["caller_id"] == ALICE
    r = d.handle(f"cancel {ref}", "m", caller_id=MALLORY)
    assert (r.action, r.text) == NOT_FOUND
    r2 = d.handle("cancel 00000000-0000-4000-8000-000000000000", "m", caller_id=MALLORY)
    assert (r2.action, r2.text) == (r.action, r.text)
    assert [b["id"] for b in store.active()] == [ref]
    r = d.handle(f"cancel {ref}", "a2", caller_id=ALICE)     # same phone, new call
    assert r.action == "cancelled"


def test_dialogue_reschedule_by_other_caller_refused(d, store):
    ref = _book(d, caller_id=ALICE)
    d.handle(f"reschedule {ref} 2027-12-13 11:00", "m", caller_id=MALLORY)
    assert (lambda r: (r.action, r.text))(d.handle("yes", "m", caller_id=MALLORY)) == NOT_FOUND
    assert [b["id"] for b in store.active()] == [ref]
    d.handle(f"reschedule {ref} 2027-12-13 11:00", "a", caller_id=ALICE)
    r = d.handle("yes", "a", caller_id=ALICE)
    assert r.action == "rescheduled"
    assert store.active()[0]["caller_id"] == ALICE


@pytest.mark.parametrize("text", ["book 2027-12-10 10:00",
                                  "cancel 00000000-0000-4000-8000-000000000000",
                                  "reschedule 00000000-0000-4000-8000-000000000000 2027-12-13 11:00"])
def test_require_caller_id_refuses_anonymous_writes(store, text):
    d = Dialogue(store, require_caller_id=True)
    r = d.handle(text, "s")
    assert (r.action, r.text, r.committed_id) == (
        "verification_required",
        "I need the number you are calling from before I can do that. Please call from your own phone.",
        None)
    assert d.handle("yes", "s").text == "There is no pending booking to confirm."
    assert d.handle(text, "s", caller_id=ALICE).action != "verification_required"


def test_require_caller_id_defaults_off(d):
    assert d.handle("book 2027-12-10 10:00", "s").action == "confirm_required"


def test_migration_adds_caller_id_to_existing_db(tmp_path):
    path = str(tmp_path / "old.sqlite")
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE bookings (id TEXT PRIMARY KEY, resource TEXT NOT NULL, starts TEXT NOT NULL,
        ends TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
        idem_key TEXT UNIQUE NOT NULL, payload_key TEXT NOT NULL)""")
    db.execute("INSERT INTO bookings VALUES('old','r','2027-12-10T15:00:00+00:00','2027-12-10T15:30:00+00:00','active','k','p')")
    db.commit(); db.close()
    store = BookingStore(path)
    BookingStore(path)                                    # re-opening is idempotent
    cols = [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(bookings)")]
    assert cols[-1] == "caller_id" and cols.count("caller_id") == 1
    (row,) = store.active()
    assert (row["id"], row["caller_id"]) == ("old", None)
    assert store.cancel("old", caller_id=ALICE) is False   # legacy row: no owner to match
    assert store.cancel("old") is True


# ---- 4. DST through the reschedule path ---------------------------------------------
ALL_DAY = {day: (time(0), time(23, 59)) for day in range(7)}
DST_MSG = ("invalid", "Please choose an unambiguous date and time. "
           "Ambiguous or nonexistent local time due to daylight saving time")


@pytest.mark.parametrize("book,move,old_utc,new_utc", [
    # fall back: Fri 10:00 EDT -> Mon 10:00 EST
    ("book 2027-11-05 10:00", "2027-11-08 10:00", "2027-11-05T14:00:00+00:00", "2027-11-08T15:00:00+00:00"),
    # spring forward: Fri 10:00 EST -> Mon 10:00 EDT
    ("book 2027-03-12 10:00", "2027-03-15 10:00", "2027-03-12T15:00:00+00:00", "2027-03-15T14:00:00+00:00"),
    # and back again across the same change
    ("book 2027-11-08 10:00", "2027-11-05 10:00", "2027-11-08T15:00:00+00:00", "2027-11-05T14:00:00+00:00"),
])
def test_reschedule_across_dst_keeps_local_wall_time(d, store, book, move, old_utc, new_utc):
    ref = _book(d, book)
    assert store.active()[0]["starts"] == old_utc
    d.handle(f"reschedule {ref} {move}", "s")
    assert d.handle("yes", "s").action == "rescheduled"
    (row,) = store.active()
    assert row["starts"] == new_utc


@pytest.mark.parametrize("when", ["2027-11-07 01:30", "2027-11-07 01:00", "2027-11-07 01:59",
                                  "2027-03-14 02:30", "2027-03-14 02:00", "2027-03-14 02:59"])
def test_reschedule_into_fold_or_gap_refused_and_original_kept(store, when):
    d = Dialogue(store, hours=ALL_DAY)
    ref = _book(d, "book 2027-11-05 10:00")
    r = d.handle(f"reschedule {ref} {when}", "s")
    assert (r.action, r.text) == DST_MSG
    assert d.handle("yes", "s").text == "There is no pending booking to confirm."
    (row,) = store.active()
    assert (row["id"], row["starts"]) == (ref, "2027-11-05T14:00:00+00:00")


@pytest.mark.parametrize("when,starts,ends", [
    ("2027-11-07 00:30 60", "2027-11-07T04:30:00+00:00", "2027-11-07T05:30:00+00:00"),  # ends in the fold
    ("2027-11-07 00:59 60", "2027-11-07T04:59:00+00:00", "2027-11-07T05:59:00+00:00"),
    ("2027-11-07 02:00", "2027-11-07T07:00:00+00:00", "2027-11-07T07:30:00+00:00"),     # just after the fold
    ("2027-03-14 01:30 60", "2027-03-14T06:30:00+00:00", "2027-03-14T07:30:00+00:00"),  # spans the gap
    ("2027-03-14 01:45 30", "2027-03-14T06:45:00+00:00", "2027-03-14T07:15:00+00:00"),
    ("2027-03-14 03:00", "2027-03-14T07:00:00+00:00", "2027-03-14T07:30:00+00:00"),     # just after the gap
])
def test_reschedule_spanning_dst_uses_real_elapsed_minutes(store, when, starts, ends):
    d = Dialogue(store, hours=ALL_DAY)
    ref = _book(d, "book 2027-11-05 10:00")
    assert d.handle(f"reschedule {ref} {when}", "s").action == "confirm_required"
    r = d.handle("yes", "s")
    assert r.action == "rescheduled"
    (row,) = store.active()
    assert (row["starts"], row["ends"]) == (starts, ends)


def test_book_spanning_gap_and_fold_directly(store):
    d = Dialogue(store, hours=ALL_DAY)
    _book(d, "book 2027-03-14 01:30 60")
    _book(d, "book 2027-11-07 00:30 60")
    assert [(b["starts"], b["ends"]) for b in store.active()] == [
        ("2027-03-14T06:30:00+00:00", "2027-03-14T07:30:00+00:00"),
        ("2027-11-07T04:30:00+00:00", "2027-11-07T05:30:00+00:00")]


# ---- 6. idempotency replay through Dialogue ------------------------------------------
# A Dialogue rebuilt for the same session (process restart, server LRU eviction)
# restarts its turn counter, so it reuses the same session:turn idempotency key.

def test_exact_replay_after_restart_returns_same_booking(store):
    first = _book(Dialogue(store), "book 2027-12-10 10:00")
    again = Dialogue(store)
    again.handle("book 2027-12-10 10:00", "s")
    r = again.handle("yes", "s")
    assert (r.action, r.committed_id) == ("booked", first)
    assert [b["id"] for b in store.active()] == [first]


def test_changed_payload_after_restart_is_a_new_booking_not_the_old_one(store):
    first = _book(Dialogue(store), "book 2027-12-10 10:00")
    again = Dialogue(store)
    again.handle("book 2027-12-10 11:00", "s")
    r = again.handle("yes", "s")
    assert r.action == "booked" and r.committed_id not in (None, first)
    assert r.text == f"Confirmed for 2027-12-10 11:00 AM. Your reference is {r.committed_id}."
    assert [(b["id"], b["starts"]) for b in store.active()] == [
        (first, "2027-12-10T15:00:00+00:00"), (r.committed_id, "2027-12-10T16:00:00+00:00")]


def test_changed_payload_replay_for_reschedule_after_restart(store):
    d = Dialogue(store)
    a = _book(d, "book 2027-12-10 10:00", session="a")
    b = _book(d, "book 2027-12-10 12:00", session="b")
    d.handle(f"reschedule {a} 2027-12-13 10:00", "s")
    moved_a = d.handle("yes", "s").committed_id
    again = Dialogue(store)                       # same session "s", turn counter reset
    again.handle(f"reschedule {b} 2027-12-13 11:00", "s")
    r = again.handle("yes", "s")
    assert r.action == "rescheduled" and r.committed_id not in (None, moved_a, b)
    assert sorted(x["starts"] for x in store.active()) == [
        "2027-12-13T15:00:00+00:00", "2027-12-13T16:00:00+00:00"]


def test_booking_key_conflict_never_reports_another_booking(store, monkeypatch):
    d = Dialogue(store)
    keys = []
    real = store.book
    monkeypatch.setattr(store, "book", lambda *a, **k: keys.append(a[3]) or real(*a, **k))
    _book(d, "book 2027-12-10 10:00")
    _book(Dialogue(store), "book 2027-12-10 11:00")
    assert keys[0] != keys[1] and all(k.startswith("s:2:") for k in keys)
