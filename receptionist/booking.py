"""Atomic, idempotent SQLite booking store. All input times are UTC-aware."""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path

import sqlite3
import uuid

class SlotTaken(Exception): pass
class InvalidBooking(Exception): pass
class KeyConflict(Exception): pass

class BookingStore:
    def __init__(self, path: str, busy_timeout_s: float = 15.0):
        self.path = path
        self.busy_timeout_s = busy_timeout_s

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS bookings (
                id TEXT PRIMARY KEY, resource TEXT NOT NULL, starts TEXT NOT NULL,
                ends TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
                idem_key TEXT UNIQUE NOT NULL, payload_key TEXT NOT NULL
            )""")
            db.execute("CREATE INDEX IF NOT EXISTS active_slot ON bookings(resource,status,starts,ends)")

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=self.busy_timeout_s, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_s * 1000)}")
        # Power-loss durability: in WAL mode NORMAL can drop the last commits on a
        # power cut; FULL fsyncs each commit, so a confirmed booking survives.
        db.execute("PRAGMA synchronous=FULL")
        return db

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

    def book(self, resource: str, starts: datetime, ends: datetime, idem_key: str) -> dict:
        if not resource or not idem_key: raise InvalidBooking("Resource and idempotency key required")
        a,b = self._range(starts,ends)
        payload_key = f"{resource}|{a}|{b}"
        # SQLite BEGIN IMMEDIATE serializes writers even when several connections/threads race.
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                previous=db.execute("SELECT * FROM bookings WHERE idem_key=?",(idem_key,)).fetchone()
                if previous:
                    if previous['payload_key']!=payload_key: raise KeyConflict("Idempotency key reused with different slot")
                    db.execute("COMMIT")
                    return dict(previous)
                taken=db.execute("""SELECT id FROM bookings WHERE resource=? AND status='active'
                     AND starts < ? AND ends > ? LIMIT 1""",(resource,b,a)).fetchone()
                if taken: raise SlotTaken("Requested slot is already taken")
                key=str(uuid.uuid4())
                db.execute("INSERT INTO bookings(id,resource,starts,ends,status,idem_key,payload_key) VALUES(?,?,?,?,?,?,?)",
                           (key,resource,a,b,'active',idem_key,payload_key))
                row=db.execute("SELECT * FROM bookings WHERE id=?",(key,)).fetchone()
                db.execute("COMMIT")
                return dict(row)
            except Exception:
                # BEGIN may itself have failed (e.g. locked); never mask that error.
                if db.in_transaction: db.execute("ROLLBACK")
                raise

    def reschedule(self, reference: str, starts: datetime, ends: datetime, idem_key: str) -> dict:
        """Move an active booking in ONE transaction: the old row is retired and the
        new row inserted together, or nothing changes (e.g. new slot taken)."""
        if not reference or not idem_key: raise InvalidBooking("Reference and idempotency key required")
        a,b = self._range(starts,ends)
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                old=db.execute("SELECT * FROM bookings WHERE id=?",(reference,)).fetchone()
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
                db.execute("INSERT INTO bookings(id,resource,starts,ends,status,idem_key,payload_key) VALUES(?,?,?,?,?,?,?)",
                           (key,resource,a,b,'active',idem_key,payload_key))
                row=db.execute("SELECT * FROM bookings WHERE id=?",(key,)).fetchone()
                db.execute("COMMIT")
                return dict(row)
            except Exception:
                # BEGIN may itself have failed (e.g. locked); never mask that error.
                if db.in_transaction: db.execute("ROLLBACK")
                raise

    def cancel(self, reference: str) -> bool:
        # The UUID is an opaque bearer reference; callers must possess the complete reference.
        with self._connect() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                n=db.execute("UPDATE bookings SET status='cancelled' WHERE id=? AND status='active'",(reference,)).rowcount
                db.execute("COMMIT")
                return n==1
            except Exception:
                # BEGIN may itself have failed (e.g. locked); never mask that error.
                if db.in_transaction: db.execute("ROLLBACK")
                raise

    def active(self) -> list[dict]:
        with self._connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM bookings WHERE status='active' ORDER BY starts")]
