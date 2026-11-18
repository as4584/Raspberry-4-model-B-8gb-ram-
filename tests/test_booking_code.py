"""#38 phase 2 (contract v0.4): a 4-digit spoken booking code, unique among the
caller's active bookings. Cancelling by code REQUIRES the verified caller
number, so the short code alone never authorises anything."""
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from receptionist.booking import BookingStore
from receptionist.dialogue import Dialogue

T0 = datetime(2027, 1, 20, 14, tzinfo=timezone.utc)
CALLER, OTHER = "+15551230001", "+15551230002"
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=ZoneInfo("America/New_York"))


@pytest.fixture
def store(tmp_path):
    return BookingStore(str(tmp_path / "c.sqlite"))


def slot(i):
    return T0 + timedelta(hours=i), T0 + timedelta(hours=i, minutes=30)


def test_booking_with_caller_gets_4_digit_code(store):
    row = store.book("r", *slot(0), "k0", caller_id=CALLER)
    assert re.fullmatch(r"\d{4}", row["code"])


def test_anonymous_booking_has_no_code(store):
    assert store.book("r", *slot(0), "k0")["code"] is None


def test_codes_unique_among_callers_active_bookings(store):
    codes = [store.book("r", *slot(i), f"k{i}", caller_id=CALLER)["code"] for i in range(60)]
    assert len(set(codes)) == 60


def test_cancel_by_code_requires_matching_caller(store):
    row = store.book("r", *slot(0), "k0", caller_id=CALLER)
    assert store.cancel_by_code(row["code"], OTHER) is False
    assert store.cancel_by_code(row["code"], None) is False
    assert store.cancel_by_code("0000" if row["code"] != "0000" else "1111", CALLER) is False
    assert len(store.active()) == 1
    assert store.cancel_by_code(row["code"], CALLER) is True
    assert store.active() == []
    assert store.cancel_by_code(row["code"], CALLER) is False        # already cancelled


def test_reschedule_keeps_the_code(store):
    old = store.book("r", *slot(0), "k0", caller_id=CALLER)
    new = store.reschedule(old["id"], *slot(5), "rs", caller_id=CALLER)
    assert new["code"] == old["code"]
    assert store.cancel_by_code(old["code"], CALLER) is True


def test_migration_adds_code_column_to_existing_db(tmp_path):
    p = tmp_path / "old.sqlite"
    db = sqlite3.connect(p)
    db.execute("""CREATE TABLE bookings (id TEXT PRIMARY KEY, resource TEXT NOT NULL, starts TEXT NOT NULL,
                  ends TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active', idem_key TEXT UNIQUE NOT NULL,
                  payload_key TEXT NOT NULL, caller_id TEXT)""")
    db.execute("INSERT INTO bookings VALUES('x','r','a','b','active','k','p','+1')")
    db.commit(); db.close()
    s = BookingStore(str(p))
    cols = [c[1] for c in sqlite3.connect(p).execute("PRAGMA table_info(bookings)")]
    assert "code" in cols and len(s.active()) == 1


# --- dialogue ---------------------------------------------------------------

@pytest.fixture
def d(tmp_path):
    return Dialogue(BookingStore(str(tmp_path / "d.sqlite")), clock=lambda: NOW)


def _book(d):
    d.handle("book december tenth at two pm", "s", caller_id=CALLER)
    return d.handle("yes", "s", caller_id=CALLER)


def test_booked_speech_includes_spoken_code(d):
    r = _book(d)
    (row,) = d.store.active()
    digits = " ".join(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][int(c)] for c in row["code"])
    assert r.speech == f"You're booked for Thursday, December tenth at two p m. Your booking code is {digits}. Thank you for calling."


@pytest.mark.parametrize("fmt", ["cancel {code}", "cancel booking {code}", "Cancel booking {spaced}.", "cancel {words}",
                                 "cancel booking {words}."])
def test_cancel_by_spoken_code(d, fmt):
    _book(d)
    code = d.store.active()[0]["code"]
    words = " ".join(["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"][int(c)] for c in code)
    said = fmt.format(code=code, spaced=" ".join(code), words=words)
    r = d.handle(said, "s", caller_id=CALLER)
    assert r.action == "cancelled" and d.store.active() == []


def test_cancel_by_code_from_other_number_is_not_found(d):
    _book(d)
    code = d.store.active()[0]["code"]
    r = d.handle(f"cancel {code}", "s", caller_id=OTHER)
    assert r.action == "not_found" and len(d.store.active()) == 1


def test_cancel_by_code_without_caller_number_needs_verification(d):
    _book(d)
    code = d.store.active()[0]["code"]
    r = d.handle(f"cancel {code}", "s")
    assert r.action == "verification_required" and len(d.store.active()) == 1


def test_uuid_cancel_still_works(d):
    ref = _book(d).committed_id
    assert d.handle(f"cancel {ref}", "s", caller_id=CALLER).action == "cancelled"
