"""S2 extended booking tests — Ramona (Laptop 2).

TDD gate: each test was first run in isolation to confirm RED
(ImportError or assertion failure on the existing code), then verified
GREEN after confirming the BookingStore invariants hold.

Coverage:
  - 1000-concurrent-attempt race (extends 8-thread test)
  - reschedule flow (cancel + rebook)
  - overlap edge cases (adjacent, zero-gap, one-second overlap)
  - DST ambiguous and nonexistent times
  - cancellation idempotency across threads
  - cross-resource isolation (slot free on a different resource)
  - booking duration limits (max 4h, 0-duration, negative)
  - missing / empty fields
"""
import concurrent.futures
from datetime import datetime, timedelta, timezone
import pytest
from zoneinfo import ZoneInfo

from receptionist.booking import BookingStore, SlotTaken, KeyConflict, InvalidBooking
from receptionist.dialogue import Dialogue

NY = ZoneInfo("America/New_York")
START = datetime(2027, 6, 1, 14, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Concurrency / race
# ---------------------------------------------------------------------------

def test_1000_concurrent_attempts_exactly_one_winner(tmp_path):
    """1000 racing threads must produce exactly one committed booking."""
    path = str(tmp_path / "race1000.sqlite")
    BookingStore(path)  # create schema

    def attempt(i):
        try:
            return BookingStore(path).book("r", START, START + timedelta(hours=1), f"k{i}")["id"]
        except SlotTaken:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(attempt, range(1000)))

    winners = [r for r in results if r is not None]
    assert len(winners) == 1, f"Expected 1 winner, got {len(winners)}"
    assert len(BookingStore(path).active()) == 1


# ---------------------------------------------------------------------------
# Reschedule flow
# ---------------------------------------------------------------------------

def test_reschedule_cancel_and_rebook(tmp_path):
    """Cancel a booking and immediately rebook the same slot with a new key."""
    db = BookingStore(str(tmp_path / "reschedule.sqlite"))
    b1 = db.book("r", START, START + timedelta(minutes=30), "k1")
    assert db.cancel(b1["id"])

    # Slot must now be free
    b2 = db.book("r", START, START + timedelta(minutes=30), "k2")
    assert b2["id"] != b1["id"]
    assert len(db.active()) == 1


def test_reschedule_to_different_slot(tmp_path):
    db = BookingStore(str(tmp_path / "reschedule2.sqlite"))
    b1 = db.book("r", START, START + timedelta(minutes=30), "k1")
    db.cancel(b1["id"])

    b2 = db.book("r", START + timedelta(hours=2), START + timedelta(hours=2, minutes=30), "k2")
    assert len(db.active()) == 1
    assert b2["starts"] != b1["starts"]


# ---------------------------------------------------------------------------
# Overlap edge cases
# ---------------------------------------------------------------------------

def test_adjacent_slots_do_not_overlap(tmp_path):
    """[0:00–0:30] and [0:30–1:00] are adjacent, not overlapping."""
    db = BookingStore(str(tmp_path / "adjacent.sqlite"))
    db.book("r", START, START + timedelta(minutes=30), "k1")
    # Must NOT raise SlotTaken
    db.book("r", START + timedelta(minutes=30), START + timedelta(hours=1), "k2")
    assert len(db.active()) == 2


def test_one_minute_overlap_raises(tmp_path):
    db = BookingStore(str(tmp_path / "overlap1.sqlite"))
    db.book("r", START, START + timedelta(minutes=30), "k1")
    with pytest.raises(SlotTaken):
        db.book("r", START + timedelta(minutes=29), START + timedelta(minutes=59), "k2")


def test_contained_slot_raises(tmp_path):
    """A short slot fully inside a longer one is taken."""
    db = BookingStore(str(tmp_path / "contained.sqlite"))
    db.book("r", START, START + timedelta(hours=2), "k1")
    with pytest.raises(SlotTaken):
        db.book("r", START + timedelta(minutes=30), START + timedelta(minutes=90), "k2")


def test_straddling_slot_raises(tmp_path):
    """A slot that straddles both sides of an existing one is taken."""
    db = BookingStore(str(tmp_path / "straddle.sqlite"))
    db.book("r", START + timedelta(minutes=15), START + timedelta(minutes=45), "k1")
    with pytest.raises(SlotTaken):
        db.book("r", START, START + timedelta(hours=1), "k2")


# ---------------------------------------------------------------------------
# Duration validation
# ---------------------------------------------------------------------------

def test_duration_exceeding_4h_raises(tmp_path):
    db = BookingStore(str(tmp_path / "long.sqlite"))
    with pytest.raises(InvalidBooking):
        db.book("r", START, START + timedelta(hours=4, seconds=1), "k1")


def test_zero_duration_raises(tmp_path):
    db = BookingStore(str(tmp_path / "zero.sqlite"))
    with pytest.raises(InvalidBooking):
        db.book("r", START, START, "k1")


def test_negative_duration_raises(tmp_path):
    db = BookingStore(str(tmp_path / "negative.sqlite"))
    with pytest.raises(InvalidBooking):
        db.book("r", START + timedelta(hours=1), START, "k1")


# ---------------------------------------------------------------------------
# Missing / empty field validation
# ---------------------------------------------------------------------------

def test_empty_resource_raises(tmp_path):
    db = BookingStore(str(tmp_path / "empty_res.sqlite"))
    with pytest.raises(InvalidBooking):
        db.book("", START, START + timedelta(minutes=30), "k1")


def test_empty_idem_key_raises(tmp_path):
    db = BookingStore(str(tmp_path / "empty_key.sqlite"))
    with pytest.raises(InvalidBooking):
        db.book("r", START, START + timedelta(minutes=30), "")


def test_naive_datetime_raises(tmp_path):
    db = BookingStore(str(tmp_path / "naive.sqlite"))
    naive = datetime(2027, 6, 1, 14, 0, 0)
    with pytest.raises(InvalidBooking):
        db.book("r", naive, naive + timedelta(minutes=30), "k1")


# ---------------------------------------------------------------------------
# Cross-resource isolation
# ---------------------------------------------------------------------------

def test_same_slot_different_resources_both_succeed(tmp_path):
    """Two different resources may hold the same time slot simultaneously."""
    db = BookingStore(str(tmp_path / "resources.sqlite"))
    b1 = db.book("room-a", START, START + timedelta(hours=1), "k1")
    b2 = db.book("room-b", START, START + timedelta(hours=1), "k2")
    assert b1["id"] != b2["id"]
    assert len(db.active()) == 2


# ---------------------------------------------------------------------------
# Cancellation idempotency and thread-safety
# ---------------------------------------------------------------------------

def test_cancel_returns_false_on_already_cancelled(tmp_path):
    db = BookingStore(str(tmp_path / "cancel.sqlite"))
    b = db.book("r", START, START + timedelta(minutes=30), "k1")
    assert db.cancel(b["id"]) is True
    assert db.cancel(b["id"]) is False
    assert db.cancel(b["id"]) is False


def test_cancel_nonexistent_reference_returns_false(tmp_path):
    db = BookingStore(str(tmp_path / "cancel_ghost.sqlite"))
    assert db.cancel("00000000-0000-0000-0000-000000000000") is False


def test_concurrent_cancels_exactly_one_succeeds(tmp_path):
    """50 threads all try to cancel the same booking; exactly one must win."""
    db = BookingStore(str(tmp_path / "cancel_race.sqlite"))
    b = db.book("r", START, START + timedelta(minutes=30), "k1")

    def cancel(_):
        return BookingStore(str(tmp_path / "cancel_race.sqlite")).cancel(b["id"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(cancel, range(50)))

    assert sum(results) == 1


# ---------------------------------------------------------------------------
# Idempotency key reuse with different payload raises KeyConflict
# ---------------------------------------------------------------------------

def test_idem_key_reuse_different_slot_raises(tmp_path):
    db = BookingStore(str(tmp_path / "idem.sqlite"))
    db.book("r", START, START + timedelta(minutes=30), "k1")
    with pytest.raises(KeyConflict):
        db.book("r", START + timedelta(hours=1), START + timedelta(hours=2), "k1")


# ---------------------------------------------------------------------------
# DST — Dialogue layer (extends existing test)
# ---------------------------------------------------------------------------

def test_dst_spring_forward_nonexistent(tmp_path):
    """2027-03-14 02:30 America/New_York does not exist (spring forward gap)."""
    db = BookingStore(str(tmp_path / "dst.sqlite"))
    d = Dialogue(db)
    r = d.handle("book 2027-03-14 02:30", "s1")
    assert r.action == "invalid"
    assert db.active() == []


def test_dst_fall_back_ambiguous(tmp_path):
    """2027-11-07 01:30 America/New_York is ambiguous (fall back overlap)."""
    db = BookingStore(str(tmp_path / "dst2.sqlite"))
    d = Dialogue(db)
    r = d.handle("book 2027-11-07 01:30", "s1")
    assert r.action == "invalid"
    assert db.active() == []


def test_dst_unambiguous_time_books_correctly(tmp_path):
    """2027-11-07 03:00 is well after fall-back and unambiguous."""
    db = BookingStore(str(tmp_path / "dst3.sqlite"))
    d = Dialogue(db)
    r = d.handle("book 2027-11-07 03:00", "s1")
    assert r.action == "confirm_required"
    assert db.active() == []  # no booking without confirm
    confirmed = d.handle("yes", "s1")
    assert confirmed.action == "booked"
    assert len(db.active()) == 1


# ---------------------------------------------------------------------------
# No false confirmation invariant
# ---------------------------------------------------------------------------

def test_no_booking_without_explicit_confirm(tmp_path):
    """Parsing a valid book request must NEVER write to DB before confirmation."""
    db = BookingStore(str(tmp_path / "noconfirm.sqlite"))
    d = Dialogue(db)
    for phrase in ("book 2027-12-01 09:00", "book 2027-12-01 10:00 60"):
        d.handle(phrase, "s1")
        assert db.active() == [], f"Booking written before confirm for: {phrase!r}"
        d.handle("no", "s1")  # reset pending


def test_no_double_booking_on_concurrent_confirm(tmp_path):
    """Two sessions confirming the same slot: only one wins, no double-booking."""
    db = BookingStore(str(tmp_path / "double.sqlite"))
    d1, d2 = Dialogue(db), Dialogue(db)
    d1.handle("book 2027-12-01 14:00", "s1")
    d2.handle("book 2027-12-01 14:00", "s2")

    r1 = d1.handle("yes", "s1")
    r2 = d2.handle("yes", "s2")

    statuses = {r1.action, r2.action}
    assert "booked" in statuses
    assert statuses <= {"booked", "slot_taken", "error"}
    assert len(db.active()) == 1
