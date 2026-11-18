"""Strict text-first conversation state machine; model outputs never perform writes."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo
from receptionist.booking import BookingStore, SlotTaken, InvalidBooking, KeyConflict

@dataclass
class Reply:
    text: str
    action: str = "reply"
    committed_id: str | None = None

# Opening hours per local weekday (Monday=0): (open, close). Closed days are absent.
DEFAULT_HOURS:dict[int,tuple[time,time]]={day:(time(9),time(17)) for day in range(5)}
DAY_NAMES=("Mon","Tue","Wed","Thu","Fri","Sat","Sun")

def _hours_text(hours:dict[int,tuple[time,time]])->str:
    groups:list[list]=[]
    for day in sorted(hours):
        span=hours[day]
        if groups and groups[-1][1]==day-1 and groups[-1][2]==span:groups[-1][1]=day
        else:groups.append([day,day,span])
    parts=[]
    for first,last,(opens,closes) in groups:
        days=DAY_NAMES[first] if first==last else f"{DAY_NAMES[first]}-{DAY_NAMES[last]}"
        parts.append(f"{days} {opens:%H:%M}-{closes:%H:%M}")
    return ", ".join(parts)

class Dialogue:
    def __init__(self, store: BookingStore, timezone_name: str = "America/New_York",
                 hours: dict[int,tuple[time,time]] | None = None,
                 durations: tuple[int,...] = (15,30,60), default_duration: int = 30):
        self.store=store
        self.tz=ZoneInfo(timezone_name)
        self.hours=DEFAULT_HOURS if hours is None else hours
        self.durations=durations
        self.default_duration=default_duration
        self._closed_text=f"That time is outside our business hours. We are open {_hours_text(self.hours)}."
        allowed=", ".join(str(x) for x in durations[:-1])
        self._duration_text=f"Please choose a duration of {allowed} or {durations[-1]} minutes."
        # Per-session state: one caller's yes/no must never touch another's pending slot.
        self._pending:dict[str,tuple[datetime,datetime]]={}
        self._turns:dict[str,int]={}

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

    def _within_hours(self, starts: datetime, ends: datetime) -> bool:
        local_start=starts.astimezone(self.tz);local_end=ends.astimezone(self.tz)
        span=self.hours.get(local_start.weekday())
        if span is None or local_end.date()!=local_start.date():return False
        return span[0]<=local_start.time() and local_end.time()<=span[1]

    def handle(self, utterance: str, session_id: str) -> Reply:
        turn=self._turns[session_id]=self._turns.get(session_id,0)+1
        s=utterance.strip().lower()
        if s in ("no","never mind","cancel that"):
            self._pending.pop(session_id,None)
            return Reply("Okay, no appointment was created.")
        if s in ("yes","confirm"):
            pending=self._pending.pop(session_id,None)
            if not pending:
                return Reply("There is no pending booking to confirm.")
            starts,ends=pending
            try:
                # Stable within a call/turn even if the network retries the same operation.
                result=self.store.book("primary",starts,ends,f"{session_id}:{turn}")
                return Reply(f"Confirmed for {starts.astimezone(self.tz):%Y-%m-%d %I:%M %p}. Your reference is {result['id']}.","booked",result['id'])
            except SlotTaken:
                return Reply("That time was just taken. Please choose another slot.","slot_taken")
            except (InvalidBooking,KeyConflict):
                return Reply("I could not safely complete that appointment.","error")
        match=re.fullmatch(r"book\s+(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)(?:\s+(\d{1,3}))?",s)
        if match:
            try:
                starts=self._parse_datetime(match.group(1),match.group(2))
                duration=int(match.group(3) or self.default_duration)
                if duration not in self.durations:
                    self._pending.pop(session_id,None)
                    return Reply(self._duration_text,"invalid")
                if starts.astimezone(timezone.utc)<=datetime.now(timezone.utc):
                    return Reply("Please choose a future appointment time.")
                # Duration is real elapsed time, computed in UTC (DST-safe).
                ends=(starts.astimezone(timezone.utc)+timedelta(minutes=duration)).astimezone(self.tz)
                if not self._within_hours(starts,ends):
                    self._pending.pop(session_id,None)
                    return Reply(self._closed_text,"closed")
                self._pending[session_id]=(starts,ends)
                return Reply(f"Please confirm: {starts:%A, %B %d at %I:%M %p} for {duration} minutes. Say yes or no.","confirm_required")
            except (ValueError,InvalidBooking) as exc:
                self._pending.pop(session_id,None)
                return Reply(f"Please choose an unambiguous date and time. {exc}","invalid")
        if s.startswith("cancel "):
            self._pending.pop(session_id,None)
            reference=s.removeprefix("cancel ").strip()
            if self.store.cancel(reference):return Reply("The booking with that reference has been cancelled.","cancelled")
            return Reply("I couldn't find an active appointment with that reference.","not_found")
        if "human" in s or "operator" in s:
            return Reply("I can arrange a human callback. Please contact the business directly if urgent.","handoff")
        return Reply("To book, say: book 2026-12-01 14:00. I will confirm before creating it.","help")
