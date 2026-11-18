"""Edge cases found by mutation testing (mutmut, 2026-11-17): these behaviours
were executed but never asserted, so injected bugs survived."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from receptionist.booking import BookingStore, InvalidBooking, SlotTaken
from receptionist.dialogue import Dialogue

T0 = datetime(2027, 1, 20, 14, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return BookingStore(str(tmp_path / "e.sqlite"))


def test_zero_length_booking_rejected(store):
    with pytest.raises(InvalidBooking, match="End must be after start"):
        store.book("r", T0, T0, "k")


def test_reversed_range_rejected(store):
    with pytest.raises(InvalidBooking, match="End must be after start"):
        store.book("r", T0, T0 - timedelta(minutes=1), "k")


def test_four_hour_limit_is_inclusive(store):
    assert store.book("r", T0, T0 + timedelta(hours=4), "ok")
    with pytest.raises(InvalidBooking, match="longer than 4 hours"):
        store.book("r", T0 + timedelta(days=1), T0 + timedelta(days=1, hours=4, seconds=1), "too-long")


@pytest.mark.parametrize("resource,key", [("", "k"), ("r", ""), ("", "")])
def test_book_requires_both_resource_and_key(store, resource, key):
    with pytest.raises(InvalidBooking, match="Resource and idempotency key required"):
        store.book(resource, T0, T0 + timedelta(minutes=30), key)
    assert store.active() == []


@pytest.mark.parametrize("ref,key", [("", "k"), ("x", ""), ("", "")])
def test_reschedule_requires_both_reference_and_key(store, ref, key):
    with pytest.raises(InvalidBooking, match="Reference and idempotency key required"):
        store.reschedule(ref, T0, T0 + timedelta(minutes=30), key)


@pytest.mark.parametrize("bad", ["starts", "ends"])
def test_naive_datetimes_rejected(store, bad):
    s, e = T0, T0 + timedelta(minutes=30)
    if bad == "starts":
        s = s.replace(tzinfo=None)
    else:
        e = e.replace(tzinfo=None)
    with pytest.raises(InvalidBooking, match="timezone"):
        store.book("r", s, e, "k")


def test_times_stored_as_utc_iso(store):
    est = timezone(timedelta(hours=-5))
    row = store.book("r", datetime(2027, 1, 20, 9, tzinfo=est), datetime(2027, 1, 20, 9, 30, tzinfo=est), "k")
    assert row["starts"] == "2027-01-20T14:00:00+00:00"
    assert row["ends"] == "2027-01-20T14:30:00+00:00"
    assert row["status"] == "active"


def test_back_to_back_slots_do_not_overlap(store):
    store.book("r", T0, T0 + timedelta(minutes=30), "a")
    assert store.book("r", T0 + timedelta(minutes=30), T0 + timedelta(minutes=60), "b")
    assert store.book("r", T0 - timedelta(minutes=30), T0, "c")
    with pytest.raises(SlotTaken):
        store.book("r", T0 + timedelta(minutes=29), T0 + timedelta(minutes=31), "d")


def test_other_resource_is_independent(store):
    store.book("r1", T0, T0 + timedelta(minutes=30), "a")
    assert store.book("r2", T0, T0 + timedelta(minutes=30), "b")


def test_cancelled_slot_can_be_rebooked(store):
    x = store.book("r", T0, T0 + timedelta(minutes=30), "a")
    assert store.cancel(x["id"])
    assert store.book("r", T0, T0 + timedelta(minutes=30), "b")


def test_creates_nested_data_directory(tmp_path):
    BookingStore(str(tmp_path / "a" / "b" / "c.sqlite"))
    assert (tmp_path / "a" / "b" / "c.sqlite").exists()


def test_uses_wal_journal(tmp_path):
    p = tmp_path / "w.sqlite"
    BookingStore(str(p))
    assert sqlite3.connect(p).execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def _break_table(store):
    """Make every write fail mid-transaction (simulates disk/IO error)."""
    db = sqlite3.connect(store.path)
    db.execute("CREATE TRIGGER boom BEFORE UPDATE ON bookings BEGIN SELECT RAISE(ABORT,'io'); END")
    db.execute("CREATE TRIGGER boom2 BEFORE INSERT ON bookings BEGIN SELECT RAISE(ABORT,'io'); END")
    db.commit()
    db.close()


def test_cancel_failure_rolls_back_and_releases_lock(store):
    x = store.book("r", T0, T0 + timedelta(minutes=30), "a")
    _break_table(store)
    with pytest.raises(sqlite3.IntegrityError, match="io"):
        store.cancel(x["id"])
    assert [r["id"] for r in store.active()] == [x["id"]]
    # Lock released: another writer can still start a transaction immediately.
    db = sqlite3.connect(store.path, timeout=1, isolation_level=None)
    db.execute("BEGIN IMMEDIATE")
    db.execute("ROLLBACK")


def test_reschedule_failure_rolls_back_atomically(store):
    x = store.book("r", T0, T0 + timedelta(minutes=30), "a")
    _break_table(store)
    with pytest.raises(sqlite3.IntegrityError, match="io"):
        store.reschedule(x["id"], T0 + timedelta(hours=1), T0 + timedelta(hours=1, minutes=30), "rs")
    rows = store.active()
    assert [r["id"] for r in rows] == [x["id"]] and rows[0]["status"] == "active"


def test_book_failure_rolls_back(store):
    _break_table(store)
    with pytest.raises(sqlite3.IntegrityError, match="io"):
        store.book("r", T0, T0 + timedelta(minutes=30), "a")
    assert store.active() == []
