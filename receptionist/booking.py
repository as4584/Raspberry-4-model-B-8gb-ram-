"""Atomic, idempotent SQLite booking store. All input times are UTC-aware."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import logging
import sqlite3
import secrets
import time
import uuid

log = logging.getLogger('receptionist.call')


def _slow(op: str, t0: float, t_lock: float, t_work: float, limit: float = 0.2) -> None:
    """Log a breakdown of any booking write over `limit` seconds (lock wait / work / commit)."""
    end = time.perf_counter()
    if end - t0 > limit:
        log.warning('db op=%s total_s=%.2f lock_wait_s=%.2f work_s=%.2f commit_s=%.2f', op, end - t0,
                    t_lock - t0, t_work - t_lock, end - t_work)

class SlotTaken(Exception): pass
class InvalidBooking(Exception): pass
class KeyConflict(Exception): pass

class BookingStore:
    def __init__(self, path: str, busy_timeout_s: float = 15.0):
        self.path = path
        self.busy_timeout_s = busy_timeout_s

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._session() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY, resource TEXT NOT NULL, starts TEXT NOT NULL,
                ends TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                idem_key TEXT UNIQUE NOT NULL, payload_key TEXT NOT NULL
            )""")
            # Migration (contract v0.3): existing DB files gain the owner column in place.
            cols=[c["name"] for c in db.execute("PRAGMA table_info(bookings)")]
            if "caller_id" not in cols:
                db.execute("ALTER TABLE bookings ADD COLUMN caller_id TEXT")
            # Migration (contract v0.4): 4-digit spoken booking code, scoped to the caller.
            if "code" not in cols:
                db.execute("ALTER TABLE bookings ADD COLUMN code TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS active_slot ON bookings(resource,status,starts,ends)")
            if "name" not in cols:
                db.execute("ALTER TABLE bookings ADD COLUMN name TEXT")
            # Messages a caller asked us to pass to the owner (their words, kept for that only).
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY, created TEXT NOT NULL, caller_id TEXT, name TEXT,
                callback TEXT, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'new'
            )""")
        # Keeper connection: while one connection stays open, closing a booking's session is
        # never the *last* close, so SQLite doesn't checkpoint+fsync the whole WAL on the
        # caller's time (Pi: 2-6 s saves on the USB stick). Checkpoints run via checkpoint().
        self._keeper = sqlite3.connect(self.path, timeout=self.busy_timeout_s, check_same_thread=False)
        self._keeper.execute("PRAGMA wal_autocheckpoint=0")
        self._keeper.execute("SELECT count(*) FROM sqlite_master").fetchone()   # really open the file (no txn kept)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=self.busy_timeout_s, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_s * 1000)}")
        db.execute("PRAGMA wal_autocheckpoint=0")     # never on a caller's commit; see checkpoint()
        # Power-loss durability: in WAL mode NORMAL can drop the last commits on a
        # power cut; FULL fsyncs each commit, so a confirmed booking survives.
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextmanager
    def _session(self):
        # sqlite3's own context manager only commits/rolls back; always close too.
        db=self._connect()
        try:
            yield db
        finally:
            db.close()

    def checkpoint(self) -> None:
        """Fold the WAL into the main file (call off the call path, e.g. every few minutes)."""
        self._keeper.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        try:
            self.checkpoint()
        finally:
            self._keeper.close()

    @staticmethod
    def _dt(t: datetime) -> str:
        if t.tzinfo is None or t.utcoffset() is None:
            raise InvalidBooking("Time must include a timezone")
        return t.astimezone(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _range(starts: datetime, ends: datetime):
        a,b = BookingStore._dt(starts),BookingStore._dt(ends)
        if a>=b:
            raise InvalidBooking("End must be after start")
        if (ends-starts).total_seconds()>4*3600:
            raise InvalidBooking("Appointment longer than 4 hours")
        return a,b

    def book(self, resource: str, starts: datetime, ends: datetime, idem_key: str,
             caller_id: str | None = None) -> dict:
        if not resource or not idem_key: raise InvalidBooking("Resource and idempotency key required")
        a,b = self._range(starts,ends)
        payload_key = f"{resource}|{a}|{b}"
        # SQLite BEGIN IMMEDIATE serializes writers even when several connections/threads race.
        with self._session() as db:
            try:
                t0 = time.perf_counter()
                db.execute("BEGIN IMMEDIATE")
                t_lock = time.perf_counter()
                previous=db.execute("SELECT * FROM bookings WHERE idem_key=?",(idem_key,)).fetchone()
                if previous:
                    if previous['payload_key']!=payload_key: raise KeyConflict("Idempotency key reused with different slot")
                    db.execute("COMMIT")
                    return dict(previous)
                taken=db.execute("""SELECT id FROM bookings WHERE resource=? AND status='active'
                     AND starts < ? AND ends > ? LIMIT 1""",(resource,b,a)).fetchone()
                if taken: raise SlotTaken("Requested slot is already taken")
                key=str(uuid.uuid4())
                code=self._new_code(db,caller_id)
                db.execute("INSERT INTO bookings(id,resource,starts,ends,status,idem_key,payload_key,caller_id,code) VALUES(?,?,?,?,?,?,?,?,?)",
                           (key,resource,a,b,'active',idem_key,payload_key,caller_id,code))
                row=db.execute("SELECT * FROM bookings WHERE id=?",(key,)).fetchone()
                t_work = time.perf_counter()
                db.execute("COMMIT")
                _slow('book', t0, t_lock, t_work)
                return dict(row)
            except Exception:
                # BEGIN may itself have failed (e.g. locked); never mask that error.
                if db.in_transaction: db.execute("ROLLBACK")
                raise

    def reschedule(self, reference: str, starts: datetime, ends: datetime, idem_key: str,
                   caller_id: str | None = None) -> dict:
        """Move an active booking in ONE transaction: the old row is retired and the
        new row inserted together, or nothing changes (e.g. new slot taken)."""
        if not reference or not idem_key: raise InvalidBooking("Reference and idempotency key required")
        a,b = self._range(starts,ends)
        with self._session() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                # Only the caller who booked may move it; a mismatch looks exactly like "not found".
                old=db.execute("SELECT * FROM bookings WHERE id=? AND caller_id IS ?",(reference,caller_id)).fetchone()
                resource=old['resource'] if old else ''
                payload_key=f"{resource}|{a}|{b}|from:{reference}"
                previous=db.execute("SELECT * FROM bookings WHERE idem_key=?",(idem_key,)).fetchone()
                if previous:
                    if previous['payload_key']!=payload_key: raise KeyConflict("Idempotency key reused with different slot")
                    db.execute("COMMIT")
                    return dict(previous)
                if not old or old['status']!='active': raise InvalidBooking("No active booking with that reference")
                # The booking being moved may overlap its own old slot.
                taken=db.execute("""SELECT id FROM bookings WHERE resource=? AND status='active' AND id<>?
                     AND starts < ? AND ends > ? LIMIT 1""",(resource,reference,b,a)).fetchone()
                if taken: raise SlotTaken("Requested slot is already taken")
                db.execute("UPDATE bookings SET status='rescheduled' WHERE id=?",(reference,))
                key=str(uuid.uuid4())
                # The caller keeps the code they were told; it moves to the new row.
                db.execute("INSERT INTO bookings(id,resource,starts,ends,status,idem_key,payload_key,caller_id,code) VALUES(?,?,?,?,?,?,?,?,?)",
                           (key,resource,a,b,'active',idem_key,payload_key,caller_id,old['code']))
                row=db.execute("SELECT * FROM bookings WHERE id=?",(key,)).fetchone()
                db.execute("COMMIT")
                return dict(row)
            except Exception:
                # BEGIN may itself have failed (e.g. locked); never mask that error.
                if db.in_transaction: db.execute("ROLLBACK")
                raise

    @staticmethod
    def _new_code(db, caller_id: str | None) -> str | None:
        """4 random digits, unique among this caller's ACTIVE bookings (chosen inside the
        caller's write transaction). No caller -> no code: a code never authorises alone."""
        if not caller_id:return None
        used={r[0] for r in db.execute("SELECT code FROM bookings WHERE caller_id=? AND status='active'",(caller_id,))}
        if len(used)>=10000:raise InvalidBooking("Too many active bookings for this caller")
        while True:
            code=f"{secrets.randbelow(10000):04d}"
            if code not in used:return code

    def cancel_by_code(self, code: str, caller_id: str | None) -> bool:
        if not caller_id or not code:return False
        with self._session() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                n=db.execute("UPDATE bookings SET status='cancelled' WHERE code=? AND caller_id=? AND status='active'",(code,caller_id)).rowcount
                db.execute("COMMIT")
                return n==1
            except Exception:
                if db.in_transaction: db.execute("ROLLBACK")
                raise

    def cancel(self, reference: str, caller_id: str | None = None) -> bool:
        # The reference alone is not enough: caller_id must match the booking's owner.
        with self._session() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                n=db.execute("UPDATE bookings SET status='cancelled' WHERE id=? AND status='active' AND caller_id IS ?",(reference,caller_id)).rowcount
                db.execute("COMMIT")
                return n==1
            except Exception:
                # BEGIN may itself have failed (e.g. locked); never mask that error.
                if db.in_transaction: db.execute("ROLLBACK")
                raise

    def active(self) -> list[dict]:
        with self._session() as db:
            return [dict(x) for x in db.execute("SELECT * FROM bookings WHERE status='active' ORDER BY starts")]

    def set_name(self, reference: str, name: str, caller_id: str | None = None) -> bool:
        with self._session() as db:
            return db.execute("UPDATE bookings SET name=? WHERE id=? AND caller_id IS ?",
                              (name, reference, caller_id)).rowcount == 1

    def add_message(self, name: str | None, callback: str | None, body: str, caller_id: str | None = None) -> int:
        with self._session() as db:
            return db.execute("INSERT INTO messages (created, caller_id, name, callback, body) VALUES (?, ?, ?, ?, ?)",
                              (datetime.now(timezone.utc).isoformat(), caller_id, name, callback, body)).lastrowid

    def messages(self, status: str | None = None) -> list[dict]:
        with self._session() as db:
            q, a = ("SELECT * FROM messages WHERE status=? ORDER BY id", (status,)) if status else                 ("SELECT * FROM messages ORDER BY id", ())
            return [dict(x) for x in db.execute(q, a)]

    def mark_message(self, message_id: int, status: str) -> None:
        with self._session() as db:
            db.execute("UPDATE messages SET status=? WHERE id=?", (status, message_id))

    def set_message_callback(self, message_id: int, callback: str) -> None:
        with self._session() as db:
            db.execute("UPDATE messages SET callback=? WHERE id=?", (callback, message_id))
