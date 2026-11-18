"""S8 G8: bookings can be backed up while the service writes, and restored.
receptionist/backup.py uses SQLite's online backup API (consistent snapshot
under WAL), writes a sha256 sidecar, keeps the newest N, and restore refuses
corrupt or tampered snapshots."""
import hashlib
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from receptionist import backup
from receptionist.booking import BookingStore

T0 = datetime(2027, 1, 20, 14, tzinfo=timezone.utc)


def fill(store, n, offset=0):
    for i in range(n):
        s = T0 + timedelta(minutes=30 * (i + offset))
        store.book("r", s, s + timedelta(minutes=30), f"k{i + offset}")


def test_backup_contains_all_committed_bookings(tmp_path):
    store = BookingStore(str(tmp_path / "live.sqlite"))
    fill(store, 25)
    snap = backup.backup(store.path, tmp_path / "backups")
    assert snap.exists() and snap.suffix == ".sqlite"
    rows = sqlite3.connect(snap).execute("SELECT count(*) FROM bookings WHERE status='active'").fetchone()[0]
    assert rows == 25
    assert sqlite3.connect(snap).execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_sidecar_checksum_matches(tmp_path):
    store = BookingStore(str(tmp_path / "live.sqlite"))
    fill(store, 3)
    snap = backup.backup(store.path, tmp_path / "backups")
    digest = snap.with_suffix(".sha256").read_text().split()[0]
    assert digest == hashlib.sha256(snap.read_bytes()).hexdigest()


def test_backup_is_consistent_while_writer_runs(tmp_path):
    store = BookingStore(str(tmp_path / "live.sqlite"))
    fill(store, 5)
    stop = threading.Event()

    def writer():
        i = 1000
        while not stop.is_set():
            s = T0 + timedelta(days=30, minutes=30 * i)
            store.book("r", s, s + timedelta(minutes=30), f"w{i}")
            i += 1

    t = threading.Thread(target=writer)
    t.start()
    try:
        time.sleep(0.2)
        snaps = [backup.backup(store.path, tmp_path / "backups") for _ in range(3)]
    finally:
        stop.set()
        t.join(10)
    for snap in snaps:
        db = sqlite3.connect(snap)
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM bookings").fetchone()[0] >= 5


def test_retention_keeps_newest(tmp_path):
    store = BookingStore(str(tmp_path / "live.sqlite"))
    fill(store, 1)
    for _ in range(5):
        backup.backup(store.path, tmp_path / "b", keep=3)
        time.sleep(1.05)                        # snapshot names are second-resolution
    snaps = sorted((tmp_path / "b").glob("*.sqlite"))
    assert len(snaps) == 3 and len(list((tmp_path / "b").glob("*.sha256"))) == 3


def test_restore_round_trip(tmp_path):
    store = BookingStore(str(tmp_path / "live.sqlite"))
    fill(store, 10)
    snap = backup.backup(store.path, tmp_path / "backups")
    fill(store, 5, offset=100)                  # changes after the backup
    target = tmp_path / "restored.sqlite"
    backup.restore(snap, target)
    restored = BookingStore(str(target))
    assert len(restored.active()) == 10
    assert restored.book("r", T0 + timedelta(days=99), T0 + timedelta(days=99, minutes=30), "after-restore")


def test_restore_refuses_tampered_snapshot(tmp_path):
    store = BookingStore(str(tmp_path / "live.sqlite"))
    fill(store, 2)
    snap = backup.backup(store.path, tmp_path / "backups")
    data = bytearray(snap.read_bytes())
    data[-10] ^= 0xFF
    snap.write_bytes(bytes(data))
    with pytest.raises(backup.BackupError, match="checksum"):
        backup.restore(snap, tmp_path / "restored.sqlite")
    assert not (tmp_path / "restored.sqlite").exists()


def test_restore_never_overwrites_existing_target(tmp_path):
    store = BookingStore(str(tmp_path / "live.sqlite"))
    fill(store, 2)
    snap = backup.backup(store.path, tmp_path / "backups")
    target = tmp_path / "existing.sqlite"
    target.write_text("precious")
    with pytest.raises(backup.BackupError, match="exists"):
        backup.restore(snap, target)
    assert target.read_text() == "precious"


def test_backup_of_missing_db_fails_clearly(tmp_path):
    with pytest.raises(backup.BackupError, match="not found"):
        backup.backup(tmp_path / "nope.sqlite", tmp_path / "b")
