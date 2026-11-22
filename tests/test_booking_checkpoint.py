"""Bookings must not pay for WAL checkpoints. Pi suite: some saves took 2-6 s because
closing the last connection makes SQLite checkpoint (and fsync) the whole WAL into
the main file on the slow USB stick. A keeper connection stays open for the store's
lifetime; checkpoints run off the call path via checkpoint(). Durability is unchanged
(WAL + synchronous=FULL still fsyncs every commit)."""
import os
import sqlite3
from datetime import datetime, timedelta, timezone

from receptionist.booking import BookingStore

T0 = datetime(2030, 1, 7, 15, tzinfo=timezone.utc)


def test_closing_a_booking_session_does_not_checkpoint(tmp_path):
    db = str(tmp_path / "k.sqlite")
    s = BookingStore(db)
    s.book("r", T0, T0 + timedelta(minutes=30), "k1", caller_id="+1")
    # With a keeper connection open, the WAL survives the session close (no checkpoint).
    assert os.path.exists(db + "-wal") and os.path.getsize(db + "-wal") > 0
    s.close()


def test_background_checkpoint_folds_the_wal_and_data_survives(tmp_path):
    db = str(tmp_path / "c.sqlite")
    s = BookingStore(db)
    s.book("r", T0, T0 + timedelta(minutes=30), "k1", caller_id="+1")
    s.checkpoint()
    assert os.path.getsize(db + "-wal") == 0
    s.close()
    rows = sqlite3.connect(db).execute("select count(*) from bookings where status='active'").fetchone()[0]
    assert rows == 1


def test_commits_stay_fully_synchronous(tmp_path):
    s = BookingStore(str(tmp_path / "f.sqlite"))
    with s._session() as db:
        assert db.execute("pragma synchronous").fetchone()[0] == 2          # FULL
    s.close()
