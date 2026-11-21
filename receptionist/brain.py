"""The receptionist's brain: tiny local model + conversation manager (no cloud, no LLM).

- A ~23 MB sentence encoder (MiniLM-L6, int8 ONNX) + a linear head says WHAT the caller
  wants (~130 ms on the Pi). It never writes dates, JSON or replies.
- parse_when() pulls the day / time / part of day out of the words, including partial
  phrases ("how about tomorrow?", "two in the afternoon", "make that eleven").
- A per-call state remembers what's missing, offers really-free slots from the calendar,
  and hands complete requests to Dialogue, which still validates and confirms bookings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from receptionist import datetime_resolve as dr
from receptionist.booking import BookingStore
from receptionist.dialogue import DEFAULT_HOURS, Dialogue, Reply
from receptionist.speech_format import spoken_time

AFFIRM = re.compile(r"^(yes|yeah|yep|yup|sure|correct|right|that'?s right|sounds good|perfect|ok(ay)?|please do)\b")
NEGATE = re.compile(r"^(no|nope|nah|never ?mind|cancel that|not really)\b")
PARTS = {"morning": (time(0), time(12)), "afternoon": (time(12), time(17)), "evening": (time(17), time(23, 59))}
PREFERRED_HOURS = (10, 14, 11, 15, 9, 13, 16, 12)        # spread offers across the day
DAY_WORDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MIN_CONFIDENCE = 0.45


@dataclass
class When:
    date: date | None = None
    time: time | None = None
    part: str | None = None


def parse_when(phrase: str, now: datetime) -> When:
    t = dr._normalise(phrase)
    t = re.sub(r"\bo ?clock\b", "", t)
    w = When()
    d, roll = dr._date(t, now.date())
    if d is not None:
        if roll and d < now.date():
            d = d.replace(year=d.year + 1)
        w.date = d
    # Remove the date words so a day-of-month isn't read as an hour.
    rest = re.sub(r"\b(" + "|".join(dr.MONTHS) + r")\s+\d{1,2}\b|\b\d{1,2}\s+of\s+(" + "|".join(dr.MONTHS) + r")\b", " ", t)
    rest = re.sub(r"\b20\d\d\b", " ", rest)
    m = re.search(r"\b(\d{1,2})(?::(\d{2})|\s(\d{2})(?!\d))?\s*(am|pm|in the morning|in the afternoon|in the evening|at night)?(?=\b)", rest)
    if m:
        hour, minute, suffix = int(m.group(1)), int(m.group(2) or m.group(3) or 0), m.group(4)
        if minute < 60:
            if suffix in ("pm", "in the afternoon", "in the evening", "at night") and 1 <= hour <= 11:
                hour += 12
            elif suffix in ("am", "in the morning") and hour == 12:
                hour = 0
            elif suffix is None and 1 <= hour <= 7:
                hour += 12                                  # business hours: "at two" means 2 pm
            if 0 <= hour <= 23:
                w.time = time(hour, minute)
    if re.search(r"\bnoon|midday\b", t):
        w.time = time(12)
    for part in PARTS:
        if re.search(rf"\b{part}\b", t) and not (m and m.group(4) and part in m.group(4)):
            w.part = part
    return w


@dataclass
class _Call:
    date: date | None = None
    time: time | None = None
    part: str | None = None
    offered: list[datetime] = field(default_factory=list)


class Brain:
    def __init__(self, store: BookingStore, intents, timezone_name: str = "America/New_York",
                 hours=None, profile: dict | None = None, require_caller_id: bool = False, clock=None):
        self.store, self.intents = store, intents
        self.tz = ZoneInfo(timezone_name)
        self.hours = DEFAULT_HOURS if hours is None else hours
        self.profile = profile or {}
        self.clock = clock or (lambda: datetime.now(self.tz))
        self.dialogue = Dialogue(store, timezone_name=timezone_name, hours=self.hours,
                                 require_caller_id=require_caller_id, clock=self.clock)
        self._calls: dict[str, _Call] = {}

    # ---- answers -------------------------------------------------------------------
    def _hours_speech(self) -> str:
        days = sorted(self.hours)
        if not days:
            return "We're currently closed."
        names = [d.capitalize() for d in DAY_WORDS]
        span = f"{names[days[0]]} to {names[days[-1]]}" if days == list(range(days[0], days[-1] + 1)) else \
            ", ".join(names[d] for d in days)
        o, c = self.hours[days[0]]
        fmt = lambda t: spoken_time(datetime.combine(date.today(), t))
        return f"We're open {span}, {fmt(o)} to {fmt(c)}."

    def _faq(self, text: str) -> Reply:
        t = text.lower()
        for key, answer in (self.profile.get("faq") or {}).items():
            if key.lower() in t:
                return Reply(answer, "faq", speech=f"Sure. {answer}")
        speech = self._hours_speech()
        return Reply(speech, "faq", speech=f"Sure. {speech}")

    # ---- calendar ------------------------------------------------------------------
    def _free_slots(self, day: date, part: str | None, n: int = 3) -> list[datetime]:
        span = self.hours.get(day.weekday())
        if not span:
            return []
        taken = [(datetime.fromisoformat(r["starts"]), datetime.fromisoformat(r["ends"])) for r in self.store.active()]
        now = self.clock()
        free = []
        for h in PREFERRED_HOURS:
            start = datetime.combine(day, time(h), self.tz)
            end = start + timedelta(minutes=30)
            if not (span[0] <= start.time() and end.time() <= span[1]) or start <= now:
                continue
            if part and not (PARTS[part][0] <= start.time() < PARTS[part][1]):
                continue
            su, eu = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
            if any(a < eu and b > su for a, b in taken):
                continue
            free.append(start)
            if len(free) == n:
                break
        return sorted(free)

    def _open_at(self, day: date, at: time) -> bool:
        span = self.hours.get(day.weekday())
        end = (datetime.combine(day, at) + timedelta(minutes=30)).time()
        return bool(span) and span[0] <= at and end <= span[1] and end > at

    def _next_open_day(self, day: date) -> date | None:
        for i in range(1, 8):
            d = day + timedelta(days=i)
            if self.hours.get(d.weekday()):
                return d
        return None

    @staticmethod
    def _list(slots: list[datetime]) -> str:
        times = [spoken_time(s) for s in slots]
        return times[0] if len(times) == 1 else ", ".join(times[:-1]) + f", or {times[-1]}"

    @staticmethod
    def _day_words(day: date, today: date) -> str:
        if day == today:
            return "today"
        if day == today + timedelta(days=1):
            return "tomorrow"
        return DAY_WORDS[day.weekday()].capitalize()

    # ---- the conversation ----------------------------------------------------------
    def handle(self, utterance: str, session_id: str, caller_id: str | None = None) -> Reply:
        text = " ".join(utterance.strip().split())
        low = re.sub(r"[.!?,]+", "", text.lower()).strip()
        st = self._calls.setdefault(session_id, _Call())
        pending = session_id in self.dialogue._pending
        if pending and AFFIRM.match(low):
            return self.dialogue.handle("yes", session_id, caller_id=caller_id)
        if NEGATE.match(low):
            self._calls[session_id] = _Call()
            if pending:
                return self.dialogue.handle("no", session_id, caller_id=caller_id)
            return Reply("Okay. Is there anything else I can help with?", "reply",
                         speech="Okay. Is there anything else I can help with?")

        intent, conf = self.intents.classify(text)
        now = self.clock()
        when = parse_when(text, now)
        has_when = bool(when.date or when.time or when.part)

        if intent == "injection":
            self._calls[session_id] = _Call()
            return Reply("I can't do that, but I'm happy to help you book or answer a question.", "refuse",
                         speech="Sorry, I can't do that. But I'm happy to help you book, or answer a question.")
        if intent in ("human", "emergency"):
            if intent == "emergency":
                return Reply("If this is an emergency, please hang up and dial 911.", "handoff",
                             speech="Okay. If this is an emergency, please hang up and dial nine one one.")
            return self.dialogue.handle("human", session_id, caller_id=caller_id)
        if intent == "cancel" or low.startswith("cancel"):
            if re.search(r"\d", text) or any(w in low.split() for w in dr._UNITS):
                return self.dialogue.handle(low if low.startswith("cancel") else "cancel " + low, session_id,
                                            caller_id=caller_id)
            return Reply("What's your four digit booking code?", "ask_code",
                         speech="Sure. What's your four digit booking code?")
        if intent == "faq" and not has_when:
            return self._faq(text)
        booking_turn = intent in ("book", "availability", "reschedule") and conf >= MIN_CONFIDENCE
        follow_up = has_when and (st.date or st.time or st.offered or pending)
        if not (booking_turn or follow_up or has_when):
            return Reply("I'm not sure about that. I can have someone call you back, or help you book.", "unknown",
                         speech="Hmm, I'm not sure about that. I can have someone call you back, or help you book.")

        # Merge what this turn said into what we already know.
        if when.date:
            st.date, st.offered = when.date, []
        if when.time:
            st.time = when.time
        if when.part:
            st.part = when.part
        if st.date and st.time:
            if self._open_at(st.date, st.time):
                starts = datetime.combine(st.date, st.time)
                return self.dialogue.handle(f"book {starts:%Y-%m-%d %H:%M}", session_id, caller_id=caller_id)
            # Closed then: never a dead end -- offer that day's openings, or the next open day's.
            day = self._day_words(st.date, now.date())
            asked, st.time = st.time, None
            if self.hours.get(st.date.weekday()):
                note = f"We're closed at {spoken_time(datetime.combine(st.date, asked))} {day}."
            else:
                note = f"We're closed {day}."
                nxt = self._next_open_day(st.date)
                if nxt is None:
                    return Reply(note, "no_slots", speech=f"Hmm, okay. {note} Would another day work?")
                st.date, day = nxt, self._day_words(nxt, now.date())
                if self._open_at(nxt, asked):
                    st.time = asked
                    return Reply(f"{note} {day} at {spoken_time(datetime.combine(nxt, asked))}?", "offer_slots",
                                 speech=f"Hmm, okay. {note} I do have {day[0].upper() + day[1:]} at "
                                        f"{spoken_time(datetime.combine(nxt, asked))}. Would that work?")
            slots = self._free_slots(st.date, st.part)
            if not slots:
                return Reply(f"{note} Nothing else is open {day}.", "no_slots",
                             speech=f"Hmm, okay. {note} Would another day work?")
            st.offered = slots
            return Reply(f"{note} {day}: {self._list(slots)}", "offer_slots",
                         speech=f"Hmm, okay. {note} {day[0].upper() + day[1:]} I have {self._list(slots)}. Which works best?")
        if st.date:
            slots = self._free_slots(st.date, st.part)
            day = self._day_words(st.date, now.date())
            if not slots:
                return Reply(f"Sorry, nothing is open {day}.", "no_slots",
                             speech=f"Hmm, okay. Sorry, nothing is open {day}. Would another day work?")
            st.offered = slots
            return Reply(f"{day}: {self._list(slots)}", "offer_slots",
                         speech=f"Sure. {day[0].upper() + day[1:]} I have {self._list(slots)}. Which works best?")
        return Reply("What day works for you?", "ask_day", speech="Sure, I can check. What day works for you?")
