"""Power-loss safety. A Pi 4 without a UPS cannot shut down gracefully when power
is cut, so a sudden stop must be harmless: every booking the caller was told is
confirmed must survive, and the database must reopen intact.

A kill -9 in CI is a *process* crash, not a power cut; it proves transaction
atomicity and recovery. Durability across a real power cut additionally relies
on synchronous=FULL (pinned below) plus ext4 fsck.repair=yes on the Pi."""
import multiprocessing as mp
import os
import signal
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from receptionist.booking import BookingStore

T0 = datetime(2027, 1, 20, 14, tzinfo=timezone.utc)


def test_every_connection_uses_full_sync_and_wal(tmp_path):
    store = BookingStore(str(tmp_path / "d.sqlite"))
    db = store._connect()
    assert db.execute("PRAGMA synchronous").fetchone()[0] == 2      # FULL
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def _writer(path, q):
    store = BookingStore(path)
    for i in range(100000):
        start = T0 + timedelta(minutes=30 * i)
        row = store.book("r", start, start + timedelta(minutes=30), f"k{i}")
        q.put(row["id"])            # only reported AFTER commit returned


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL semantics need POSIX")
@pytest.mark.parametrize("delay", [0.3, 0.7, 1.1])
def test_kill_9_mid_stream_loses_no_confirmed_booking(tmp_path, delay):
    path = str(tmp_path / "crash.sqlite")
    BookingStore(path)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_writer, args=(path, q))
    p.start()
    time.sleep(delay + 1.5)          # spawn start-up + writing
    os.kill(p.pid, signal.SIGKILL)
    p.join(10)
    confirmed = []
    while True:
        try:
            confirmed.append(q.get(timeout=0.5))
        except Exception:
            break
    assert confirmed, "writer never committed anything; test is not exercising a crash"
    db = sqlite3.connect(path)
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    ids = {r[0] for r in db.execute("SELECT id FROM bookings WHERE status='active'")}
    assert set(confirmed) <= ids                     # nothing confirmed was lost
    rows = BookingStore(path).active()               # reopens and recovers WAL
    for a, b in zip(rows, rows[1:]):
        assert a["ends"] <= b["starts"]              # still no overlaps


def _hold_lock_then_hang(path, ready):
    raw = sqlite3.connect(path, isolation_level=None)
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("INSERT INTO bookings VALUES('x','r','a','b','active','k','p')")
    ready.set()
    time.sleep(60)                   # killed before it can commit


@pytest.mark.skipif(os.name == "nt", reason="SIGKILL semantics need POSIX")
def test_store_recovers_after_crash_holding_write_lock(tmp_path):
    """Process dies mid-transaction holding the write lock (= power cut)."""
    path = str(tmp_path / "u.sqlite")
    BookingStore(path)
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    p = ctx.Process(target=_hold_lock_then_hang, args=(path, ready))
    p.start()
    assert ready.wait(30)
    os.kill(p.pid, signal.SIGKILL)
    p.join(10)
    store = BookingStore(path)
    assert store.active() == []                      # uncommitted row gone
    assert store.book("r", T0, T0 + timedelta(minutes=30), "k2")


def test_locked_database_error_is_not_masked_by_rollback(tmp_path):
    """If BEGIN IMMEDIATE itself fails, the caller must see 'locked', not a
    secondary 'cannot rollback - no transaction is active'."""
    path = str(tmp_path / "l.sqlite")
    BookingStore(path)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        store = BookingStore(path, busy_timeout_s=0.2)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.book("r", T0, T0 + timedelta(minutes=30), "k")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.cancel("anything")
    finally:
        holder.execute("ROLLBACK")
        holder.close()
