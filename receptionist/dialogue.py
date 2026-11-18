"""Strict text-first conversation state machine; model outputs never perform writes."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import re
from zoneinfo import ZoneInfo
from receptionist.datetime_resolve import resolve
from receptionist.speech_format import spoken_when, _cardinal
from receptionist.booking import BookingStore, SlotTaken, InvalidBooking, KeyConflict

@dataclass
class Reply:
    text: str
    action: str = "reply"
    committed_id: str | None = None
    speech: str | None = None   # phone wording (#38); None -> speak `text`

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

_DIGIT_WORDS={w:str(i) for i,w in enumerate(["zero","one","two","three","four","five","six","seven","eight","nine"])}|{"oh":"0"}
_DIGIT_WORDS["oh"]="0"

def _spoken_code(text: str) -> str | None:
    """'4729', '4 7 2 9', 'four seven two nine' and whisper's '4-7-2-9' / '4.729' /
    '4, 7, 2, 9' -> '4729'; anything that is not exactly four digits -> None."""
    digits="".join(_DIGIT_WORDS.get(t,t) for t in re.split(r"[\s,.\-]+",text) if t)
    return digits if re.fullmatch(r"\d{4}",digits) else None

def _code_speech(code: str | None) -> str:
    if not code:return ""
    return " Your booking code is "+" ".join(_cardinal(int(c)) for c in code)+"."

# 'Feel human' (#28): every spoken reply opens with one of these short, digit-free
# sentences. They are synthesised once and cached, so audio starts as soon as the
# words are understood, and each is ~1 s long, covering synthesis of what follows.
OPENERS=("Okay, let me check that.","Great, you're all set.","Okay, all done.")

class Dialogue:
    def __init__(self, store: BookingStore, timezone_name: str = "America/New_York",
                 hours: dict[int,tuple[time,time]] | None = None,
                 durations: tuple[int,...] = (15,30,60), default_duration: int = 30,
                 require_caller_id: bool = False, clock=None):
        self.store=store
        # Injectable clock (tests); real time by default.
        self._now=clock or (lambda: datetime.now(timezone.utc))
        self.tz=ZoneInfo(timezone_name)
        self.hours=DEFAULT_HOURS if hours is None else hours
        self.durations=durations
        self.default_duration=default_duration
        # Production should set this: anonymous callers may then not book, cancel or move.
        self.require_caller_id=require_caller_id
        self._closed_text=f"That time is outside our business hours. We are open {_hours_text(self.hours)}."
        allowed=", ".join(str(x) for x in durations[:-1])
        self._duration_text=f"Please choose a duration of {allowed} or {durations[-1]} minutes."
        # Per-session state: one caller's yes/no must never touch another's pending slot.
        self._pending:dict[str,tuple[str|None,datetime,datetime]]={}  # (reschedule ref or None, starts, ends)
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

    def _slot(self, ymd: str, hm: str, minutes: str | None, session_id: str):
        """Validate a requested slot; returns (starts, ends, duration) or a Reply refusal."""
        self._pending.pop(session_id,None)
        try:
            starts=self._parse_datetime(ymd,hm)
        except (ValueError,InvalidBooking) as exc:
            return Reply(f"Please choose an unambiguous date and time. {exc}","invalid")
        duration=int(minutes or self.default_duration)
        if duration not in self.durations:
            return Reply(self._duration_text,"invalid")
        if starts.astimezone(timezone.utc)<=self._now().astimezone(timezone.utc):
            return Reply("Please choose a future appointment time.")
        # Duration is real elapsed time, computed in UTC (DST-safe).
        ends=(starts.astimezone(timezone.utc)+timedelta(minutes=duration)).astimezone(self.tz)
        if not self._within_hours(starts,ends):
            return Reply(self._closed_text,"closed")
        return starts,ends,duration

    def handle(self, utterance: str, session_id: str, caller_id: str | None = None) -> Reply:
        turn=self._turns[session_id]=self._turns.get(session_id,0)+1
        # ASR punctuates ("Yes.", "Cancel that."): drop trailing punctuation before matching.
        s=re.sub(r"[\s.!?,;]+$","",utterance.strip().lower())
        if self.require_caller_id and not caller_id and re.match(r"(book|cancel|reschedule) ",s):
            self._pending.pop(session_id,None)
            return Reply("I need the number you are calling from before I can do that. Please call from your own phone.","verification_required")
        if s in ("no","never mind","cancel that"):
            pending=self._pending.pop(session_id,None)
            if pending and pending[0]:
                return Reply("Okay, your appointment was not changed.")
            return Reply("Okay, no appointment was created.")
        if s in ("yes","confirm"):
            pending=self._pending.pop(session_id,None)
            if not pending:
                return Reply("There is no pending booking to confirm.")
            reference,starts,ends=pending
            # Stable for a retried identical operation; the operation is part of the key so a
            # rebuilt session reusing a turn number with a different request is not a conflict.
            key=f"{session_id}:{turn}:{reference or 'book'}@{starts.astimezone(timezone.utc):%Y%m%dT%H%M}-{ends.astimezone(timezone.utc):%Y%m%dT%H%M}"
            try:
                if reference:
                    result=self.store.reschedule(reference,starts,ends,key,caller_id=caller_id)
                    return Reply(f"Rescheduled to {starts:%Y-%m-%d %I:%M %p}. Your new reference is {result['id']}.","rescheduled",result['id'],
                                 speech=f"Okay, all done. Your appointment is now {spoken_when(starts,self.tz)}.")
                result=self.store.book("primary",starts,ends,key,caller_id=caller_id)
                return Reply(f"Confirmed for {starts:%Y-%m-%d %I:%M %p}. Your reference is {result['id']}.","booked",result['id'],
                             speech=f"Great, you're all set. {spoken_when(starts,self.tz)}.{_code_speech(result.get('code'))} Thank you for calling.")
            except SlotTaken:
                return Reply("That time was just taken. Please choose another slot.","slot_taken")
            except InvalidBooking:
                if reference:
                    return Reply("I couldn't find an active appointment with that reference.","not_found")
                return Reply("I could not safely complete that appointment.","error")
            except KeyConflict:
                return Reply("I could not safely complete that appointment.","error")
        match=re.fullmatch(r"(book|reschedule\s+(\S+))\s+(\d{4}-\d\d-\d\d)\s+(\d\d:\d\d)(?:\s+(\d{1,3}))?",s)
        if match:
            slot=self._slot(match.group(3),match.group(4),match.group(5),session_id)
            if isinstance(slot,Reply):return slot
            starts,ends,duration=slot
            reference=match.group(2)
            self._pending[session_id]=(reference,starts,ends)
            if reference:
                return Reply(f"Please confirm moving your appointment to {starts:%A, %B %d at %I:%M %p} for {duration} minutes. Say yes or no.","confirm_required")
            return Reply(f"Please confirm: {starts:%A, %B %d at %I:%M %p} for {duration} minutes. Say yes or no.","confirm_required",
                         speech=f"Okay, let me check that. {spoken_when(starts,self.tz)}. For {_cardinal(duration)} minutes. Shall I book it?")
        if s=="book" or s.startswith("book "):
            # Spoken request: the deterministic resolver decides the instant (or we ask).
            when=resolve(s.removeprefix("book").strip(),self._now().astimezone(self.tz))
            if when is None:
                self._pending.pop(session_id,None)
                return Reply("What day and time would you like? For example: December tenth at two p m.","clarify")
            slot=self._slot(f"{when:%Y-%m-%d}",f"{when:%H:%M}",None,session_id)
            if isinstance(slot,Reply):return slot
            starts,ends,duration=slot
            self._pending[session_id]=(None,starts,ends)
            return Reply(f"Please confirm: {starts:%A, %B %d at %I:%M %p} for {duration} minutes. Say yes or no.","confirm_required",
                         speech=f"Okay, let me check that. {spoken_when(starts,self.tz)}. For {_cardinal(duration)} minutes. Shall I book it?")
        if s.startswith("cancel "):
            self._pending.pop(session_id,None)
            reference=s.removeprefix("cancel ").strip()
            code=_spoken_code(reference.removeprefix("booking ").strip())
            if code:
                # Short spoken code: only valid together with the verified caller number.
                if not caller_id:
                    return Reply("I need the number you are calling from before I can do that. Please call from your own phone.","verification_required")
                if self.store.cancel_by_code(code,caller_id):return Reply("The booking with that reference has been cancelled.","cancelled")
                return Reply("I couldn't find an active appointment with that reference.","not_found")
            if self.store.cancel(reference,caller_id=caller_id):return Reply("The booking with that reference has been cancelled.","cancelled")
            return Reply("I couldn't find an active appointment with that reference.","not_found")
        if "human" in s or "operator" in s:
            return Reply("I can arrange a human callback. Please contact the business directly if urgent.","handoff")
        return Reply("To book, say: book 2026-12-01 14:00. I will confirm before creating it.","help",
                     speech="Tell me the day and time you'd like, for example, December tenth at two p m.")
