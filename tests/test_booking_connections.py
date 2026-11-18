"""Every SQLite connection BookingStore opens must be closed when the operation
ends, on success and on every error path (CI on 3.13 showed thousands of
'ResourceWarning: unclosed database' from booking.py)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from receptionist.booking import BookingStore, InvalidBooking, KeyConflict, SlotTaken

T0 = datetime(2027, 1, 20, 14, tzinfo=timezone.utc)
M30 = timedelta(minutes=30)


@pytest.fixture
def opened(monkeypatch):
    conns = []
    real = BookingStore._connect

    def spy(self):
        conn = real(self)
        conns.append(conn)
        return conn
    monkeypatch.setattr(BookingStore, "_connect", spy)
    return conns


def assert_all_closed(conns):
    assert conns, "store opened no connections"
    for conn in conns:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            conn.execute("select 1")


def test_init_closes_connection(tmp_path, opened):
    BookingStore(str(tmp_path / "c.sqlite"))
    assert len(opened) == 1
    assert_all_closed(opened)


def test_happy_paths_close_connections(tmp_path, opened):
    store = BookingStore(str(tmp_path / "c.sqlite"))
    ref = store.book("r", T0, T0 + M30, "k")["id"]
    store.book("r", T0, T0 + M30, "k")                       # replay
    new = store.reschedule(ref, T0 + 2 * M30, T0 + 3 * M30, "rs")["id"]
    store.reschedule(ref, T0 + 2 * M30, T0 + 3 * M30, "rs")  # replay
    assert [b["id"] for b in store.active()] == [new]
    assert store.cancel(new) is True
    assert store.cancel(new) is False
    assert len(opened) == 8
    assert_all_closed(opened)


def test_error_paths_close_connections(tmp_path, opened):
    store = BookingStore(str(tmp_path / "c.sqlite"))
    ref = store.book("r", T0, T0 + M30, "k")["id"]
    other = store.book("r", T0 + 2 * M30, T0 + 3 * M30, "k2")["id"]
    with pytest.raises(SlotTaken):
        store.book("r", T0, T0 + M30, "k3")
    with pytest.raises(KeyConflict):
        store.book("r", T0 + 4 * M30, T0 + 5 * M30, "k")
    with pytest.raises(SlotTaken):
        store.reschedule(ref, T0 + 2 * M30, T0 + 3 * M30, "rs")
    with pytest.raises(InvalidBooking):
        store.reschedule("missing", T0 + 6 * M30, T0 + 7 * M30, "rs2")
    store.reschedule(other, T0 + 6 * M30, T0 + 7 * M30, "rs3")
    with pytest.raises(KeyConflict):
        store.reschedule(other, T0 + 8 * M30, T0 + 9 * M30, "rs3")
    assert len(opened) == 9
    assert_all_closed(opened)


def test_locked_db_errors_close_connections(tmp_path, opened):
    path = str(tmp_path / "c.sqlite")
    store = BookingStore(path, busy_timeout_s=0.05)
    ref = store.book("r", T0, T0 + M30, "k")["id"]
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.book("r", T0 + 2 * M30, T0 + 3 * M30, "k2")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.cancel(ref)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.reschedule(ref, T0 + 4 * M30, T0 + 5 * M30, "rs")
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert len(opened) == 5
    assert_all_closed(opened)
