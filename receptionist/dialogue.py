"""Strict text-first conversation state machine; model outputs never perform writes."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo
from receptionist.booking import BookingStore, SlotTaken, InvalidBooking, KeyConflict

@dataclass
class Reply:
    text: str
    action: str = "reply"
    committed_id: str | None = None

class Dialogue:
    def __init__(self, store: BookingStore, timezone_name: str = "America/New_York"):
        self.store=store
        self.tz=ZoneInfo(timezone_name)
        self.pending:tuple[datetime,datetime]|None=None
        self.turn=0

    def _parse_datetime(self, ymd: str, hm: str) -> datetime:
        naive=datetime.fromisoformat(f"{ymd}T{hm}")
        # Treat ambiguous daylight-saving times conservatively.
        a=naive.replace(tzinfo=self.tz,fold=0)
        b=naive.replace(tzinfo=self.tz,fold=1)
        if a.utcoffset()!=b.utcoffset():
            raise InvalidBooking("Ambiguous or nonexistent local time due to daylight saving time")
        roundtrip=a.astimezone(timezone.utc).astimezone(self.tz).replace(tzinfo=None)
        if roundtrip!=naive: raise InvalidBooking("Nonexistent local time")
        return a

    def handle(self, utterance: str, session_id: str) -> Reply:
        self.turn+=1
        s=utterance.strip().lower()
        if s in ("no","never mind","cancel that"):
            self.pending=None
            return Reply("Okay, no appointment was created.")
        if s in ("yes","confirm"):
            if not self.pending:
                return Reply("There is no pending booking to confirm.")
            starts,ends=self.pending
            self.pending=None
            try:
                # Stable within a call/turn even if the network retries the same operation.
                result=self.store.book("primary",starts,ends,f"{session_id}:{self.turn}")
                return Reply(f"Confirmed for {starts.astimezone(self.tz):%Y-%m-%d %I:%M %p}. Your reference is {result['id']}.","booked",result['id'])
            except SlotTaken:
                return Reply("That time was just taken. Please choose another slot.","slot_taken")
            except (InvalidBooking,KeyConflict):
                return Reply("I could not safely complete that appointment.","error")
        match=re.fullmatch(r"book\s+(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)(?:\s+(15|30|60))?",s)
        if match:
            try:
                starts=self._parse_datetime(match.group(1),match.group(2))
                duration=int(match.group(3) or '30')
                if starts.astimezone(timezone.utc)<=datetime.now(timezone.utc):
                    return Reply("Please choose a future appointment time.")
                self.pending=(starts,starts+timedelta(minutes=duration))
                return Reply(f"Please confirm: {starts:%A, %B %d at %I:%M %p} for {duration} minutes. Say yes or no.","confirm_required")
            except (ValueError,InvalidBooking) as exc:
                self.pending=None
                return Reply(f"Please choose an unambiguous date and time. {exc}","invalid")
        if s.startswith("cancel "):
            self.pending=None
            reference=s.removeprefix("cancel ").strip()
            if self.store.cancel(reference):return Reply("The booking with that reference has been cancelled.","cancelled")
            return Reply("I couldn't find an active appointment with that reference.","not_found")
        if "human" in s or "operator" in s:
            return Reply("I can arrange a human callback. Please contact the business directly if urgent.","handoff")
        return Reply("To book, say: book 2026-12-01 14:00. I will confirm before creating it.","help")
