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
from receptionist.profile import answer as profile_answer
from receptionist.speech_format import spoken_time

AFFIRM = re.compile(r"^(yes|yeah|yep|yup|yo|sure|correct|right|that'?s right|sounds? good|perfect|ok(ay)?|please do)\b")
NEGATE = re.compile(r"^(no|nope|nah|never ?mind|cancel that|not really)\b")
PARTS = {"morning": (time(0), time(12)), "afternoon": (time(12), time(17)), "evening": (time(17), time(23, 59))}
PREFERRED_HOURS = (10, 14, 11, 15, 9, 13, 16, 12)        # spread offers across the day
DAY_WORDS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MIN_CONFIDENCE = 0.45
# Same meaning, different words: a person doesn't open every reply with "Sure."
VARIANTS = {"Sure.": ("Sure.", "Of course.", "Absolutely.", "Happy to help."),
            "Okay.": ("Okay.", "Alright.", "Got it."),
            "Hmm, okay.": ("Hmm, okay.", "Ah, okay.", "I see."),
            "Great, you're all set.": ("Great, you're all set.", "Perfect, you're all booked.", "Wonderful, you're all set."),
            "Okay, let me check that.": ("Okay, let me check that.", "Let me take a look.", "Sure, let me check.")}
# Cue words: the tiny model may only take drastic routes when the words support it
# (Pi suite: "Yeah." -> human, "Perfect." -> emergency, "Tomorrow is fine." -> injection).
CUES = {"injection": re.compile(r"\b(ignore|pretend|override|admin|system prompt|instructions|rules|jailbreak|developer mode)\b"),
        "human": re.compile(r"\b(someone|somebody|person|human|operator|representative|staff|manager|receptionist|real)\b"),
        "emergency": re.compile(r"\b(emergency|urgent|hurt|injur|bleed|pain|911|ambulance|right now)\w*"),
        "cancel": re.compile(r"\bcancel")}
BOOK_WORDS = re.compile(r"\b(book|appointment|schedule|reserve|come in|slot|opening|availability|available)\b")
SMALL_TALK = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening))?\W*(there\W*)?(how are you|how's it going|how are things)|^(hi|hello|hey)( there)?$")
GOODBYE = re.compile(r"\b(bye|goodbye|thanks,? by|thank you,? by|that's all|that is all|that'll be all|have a (good|nice|great) day)\b")
ORDINALS = {"first": 0, "earliest": 0, "1st": 0, "second": 1, "2nd": 1, "middle": 1, "third": 2, "3rd": 2,
            "last": -1, "latest": -1}
# whisper writes "three" as "free"/"pre"/"tree" (Pi suite); only rewrite when a time is
# expected (times were just offered or a day is chosen) and a time word follows.
# ... also glued to "o clock" ("Preo clock"). The "o" stays so "o clock" still parses.
THREE_MISHEARD = re.compile(r"\b(free|pre|tree|pretty)(?=o[\s'-]*clock\b|\b[\s-]*(o[\s'-]*clock|pm|p m|am|a m|works|is good|sounds|please))")
MISHEARD = [(re.compile(p, re.I), w) for p, w in (
    (r"\b(into|in to|to) ?more\b(?! than)", "tomorrow"), (r"\bto morrow\b", "tomorrow"),
    (r"\bpoint ?meant\b", "appointment"), (r"\bhour\b(?= *\?| on| today)", "hours"),
    (r"\b(puff?ake|pasaic|passaick|passay it|pass a ick)\b", "Passaic"))]
CONFIRM_TAIL = re.compile(r"\b(yes|yeah|yep|yes please|please book it|book it|that works|sounds? good)\s*(please|thank you|thanks)?\s*$")
LOCATION = re.compile(r"\b(where|address|located|location|directions|parking)\b")
OPEN_DAY = re.compile(r"\b(are you|you guys|is it|is the office)\s+open\b|\bopen on\b")


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
                 hours=None, profile: dict | None = None, require_caller_id: bool = False, clock=None,
                 seed: int | None = None):
        self.store, self.intents = store, intents
        self.tz = ZoneInfo(timezone_name)
        self.profile = profile or {}
        self.hours = hours if hours is not None else (self.profile.get('hours_parsed') or DEFAULT_HOURS)
        self.clock = clock or (lambda: datetime.now(self.tz))
        self._last: dict[str, str] = {}
        self.dialogue = Dialogue(store, timezone_name=timezone_name, hours=self.hours,
                                 require_caller_id=require_caller_id, clock=self.clock)
        self._calls: dict[str, _Call] = {}
        import random as _random
        self._rng = _random.Random(seed)

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

    def _open_on(self, day: date) -> Reply:
        name = DAY_WORDS[day.weekday()].capitalize()
        span = self.hours.get(day.weekday())
        if not span:
            speech = f"We're closed on {name}s. {self._hours_speech()}"
        else:
            fmt = lambda t: spoken_time(datetime.combine(day, t))
            speech = f"Yes, on {name}s we're open {fmt(span[0])} to {fmt(span[1])}."
        return Reply(speech, "faq", speech=f"Sure. {speech}")

    def _faq(self, text: str) -> Reply:
        topic, ans = profile_answer(self.profile, text)
        if ans:
            return Reply(ans, "faq", speech=f"Sure. {ans}")
        if topic or not re.search(r"\b(hours|open|close|closing|opening)\b", text.lower()):
            return self._unknown()
        speech = self._hours_speech()
        return Reply(speech, "faq", speech=f"Sure. {speech}")

    @staticmethod
    def _unknown() -> Reply:
        return Reply("I'm not sure about that. I can have someone call you back, or help you book.", "unknown",
                     speech="Hmm, I'm not sure about that. I can have someone call you back, or help you book.")

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
        r = self._handle(utterance, session_id, caller_id)
        # Mid-booking, a reply we can't place is almost always a misheard short answer ("Ten." -> "Pen"):
        # a person asks the question again instead of changing the subject.
        last = self._last.get(session_id)
        if r.action == "unknown" and last in ("offer_slots", "ask_which", "ask_again_time"):
            r = Reply("Sorry, which time was that?", "ask_again", speech="Sorry, which time was that?")
            last = "ask_again_time"
        elif r.action == "unknown" and last in ("ask_day", "ask_again_day"):
            r = Reply("Sorry, which day was that?", "ask_again", speech="Sorry, which day was that?")
            last = "ask_again_day"
        else:
            last = r.action
        self._last[session_id] = last
        return self._vary(r)

    def _vary(self, r: Reply) -> Reply:
        sp = r.speech or ""
        for base, options in VARIANTS.items():
            if sp.startswith(base + " ") or sp == base:
                choice = self._rng.choice(options)
                return Reply(r.text, r.action, r.committed_id, speech=choice + sp[len(base):])
        return r

    def _handle(self, utterance: str, session_id: str, caller_id: str | None = None) -> Reply:
        text = " ".join(utterance.strip().split())
        # Words phone-quality ASR mishears for common booking words.
        for heard, meant in MISHEARD:
            text = heard.sub(meant, text)
        expecting_time = self._calls.get(session_id) is not None and bool(
            self._calls[session_id].offered or self._calls[session_id].date)
        if expecting_time:
            text = THREE_MISHEARD.sub("three ", text.lower())
        low = re.sub(r"[.!?,]+", "", text.lower()).strip()
        st = self._calls.setdefault(session_id, _Call())
        pending = session_id in self.dialogue._pending
        # Whisper garbage (a decode loop like "[inainain...") or nothing intelligible: ask again.
        if not re.search(r"[a-z]{2,}|[0-9]", low) or re.search(r"(\w{2,4})\1{3,}", low):
            return Reply("Sorry, I didn't catch that.", "repeat",
                         speech="Sorry, I didn't catch that. Could you say that again?")
        if pending and AFFIRM.match(low):
            return self.dialogue.handle("yes", session_id, caller_id=caller_id)
        # Waiting for yes/no and heard a short word that is neither (Ryan voice: "Yes." -> "Just"):
        # a person asks again rather than guessing either way.
        if pending and len(low.split()) <= 2 and not NEGATE.match(low) \
                and not any(vars(parse_when(text, self.clock())).values()) and not GOODBYE.search(low):
            return Reply("Sorry, was that a yes?", "confirm_again", speech="Sorry, was that a yes?")
        if NEGATE.match(low) and not any(vars(parse_when(text, self.clock())).values()):
            self._calls[session_id] = _Call()
            if pending:
                return self.dialogue.handle("no", session_id, caller_id=caller_id)
            return Reply("Okay. Is there anything else I can help with?", "reply",
                         speech="Okay. Is there anything else I can help with?")

        if GOODBYE.search(low):
            self._calls[session_id] = _Call()
            return Reply("Thanks for calling. Have a great day!", "goodbye",
                         speech="Okay. Thanks for calling. Have a great day!")
        if SMALL_TALK.search(low):
            return Reply("I'm doing well, thanks for asking! How can I help you today?", "small_talk",
                         speech="Okay. I'm doing well, thanks for asking! How can I help you today?")
        now = self.clock()
        if st.offered and AFFIRM.match(low) and not parse_when(text, now).time:
            return Reply("Which time works best?", "ask_which", speech="Sure. Which time works best for you?")
        if st.offered:
            for word, i in ORDINALS.items():
                if re.search(rf"\b{word}\b", low):
                    pick = st.offered[i]
                    return self.dialogue.handle(f"book {pick:%Y-%m-%d %H:%M}", session_id, caller_id=caller_id)
        bare = AFFIRM.match(low) and not pending and not any(vars(parse_when(text, now)).values())
        if bare and st.date and st.time:          # "yes" to a single time we just proposed
            return self.dialogue.handle(f"book {datetime.combine(st.date, st.time):%Y-%m-%d %H:%M}", session_id,
                                        caller_id=caller_id)
        if bare:
            return Reply("Great! What can I help you with?", "reply", speech="Okay. What can I help you with?")

        intent, conf = self.intents.classify(text)
        if intent in CUES and not CUES[intent].search(low):
            intent, conf = ("book", 0.5) if BOOK_WORDS.search(low) else ("faq_unknown", 0.3)
        if BOOK_WORDS.search(low) and (intent in ("book", "availability", "reschedule") or intent not in CUES):
            intent, conf = (intent if intent in ("availability", "reschedule") else "book"), max(conf, 0.5)
        when = parse_when(text, now)
        has_when = bool(when.date or when.time or when.part)
        # v2 model classes (conversational moves) -- regexes above already caught the clear cases.
        if conf >= 0.6 and not has_when:
            if intent == "affirm":
                if pending:
                    return self.dialogue.handle("yes", session_id, caller_id=caller_id)
                if st.date and st.time:
                    return self.dialogue.handle(f"book {datetime.combine(st.date, st.time):%Y-%m-%d %H:%M}",
                                                session_id, caller_id=caller_id)
                if st.offered:
                    return Reply("Which time works best?", "ask_which", speech="Sure. Which time works best for you?")
                return Reply("Great! What can I help you with?", "reply", speech="Okay. What can I help you with?")
            if intent == "negate":
                self._calls[session_id] = _Call()
                if pending:
                    return self.dialogue.handle("no", session_id, caller_id=caller_id)
                return Reply("Okay. Is there anything else I can help with?", "reply",
                             speech="Okay. Is there anything else I can help with?")
            if intent == "goodbye":
                self._calls[session_id] = _Call()
                return Reply("Thanks for calling. Have a great day!", "goodbye",
                             speech="Okay. Thanks for calling. Have a great day!")
            if intent == "small_talk":
                return Reply("I'm doing well, thanks for asking! How can I help you today?", "small_talk",
                             speech="Okay. I'm doing well, thanks for asking! How can I help you today?")
            if intent == "business":
                return self._faq(text)
        if OPEN_DAY.search(low) and when.date:
            return self._open_on(when.date)
        if LOCATION.search(low) and not has_when:
            return self._faq(text)

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
        if (intent in ("faq", "faq_unknown") or profile_answer(self.profile, text)[1]) and not has_when:
            if intent == "faq_unknown" and not profile_answer(self.profile, text)[1]:
                return self._unknown()
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
                r = self.dialogue.handle(f"book {starts:%Y-%m-%d %H:%M}", session_id, caller_id=caller_id)
                # "2 p.m. yes." -- picked the time and confirmed it in one breath.
                if r.action == "confirm_required" and CONFIRM_TAIL.search(low):
                    return self.dialogue.handle("yes", session_id, caller_id=caller_id)
                return r
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
                    r = self.dialogue.handle(f"book {datetime.combine(nxt, asked):%Y-%m-%d %H:%M}", session_id,
                                             caller_id=caller_id)
                    if r.action == "confirm_required":
                        tail = (r.speech or r.text).split(". ", 1)[1] if ". " in (r.speech or r.text) else r.speech
                        return Reply(r.text, r.action, r.committed_id, speech=f"Hmm, okay. {note} {tail}")
                    return r
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
            note = ""
            if not slots:
                note = f"We're closed {day}. " if not self.hours.get(st.date.weekday()) else f"{day[0].upper() + day[1:]} is full. "
                nxt = st.date
                for _ in range(7):
                    nxt = self._next_open_day(nxt)
                    if nxt is None:
                        break
                    slots = self._free_slots(nxt, st.part) or self._free_slots(nxt, None)
                    if slots:
                        break
                if not slots:
                    return Reply(f"{note}Nothing is open soon.", "no_slots",
                                 speech=f"Hmm, okay. {note}Would another day work?")
                st.date, day = nxt, self._day_words(nxt, now.date())
            st.offered = slots
            if note:
                return Reply(f"{note}{day}: {self._list(slots)}", "offer_slots",
                             speech=f"Hmm, okay. {note}{day[0].upper() + day[1:]} I have {self._list(slots)}. Which works best?")
            return Reply(f"{day}: {self._list(slots)}", "offer_slots",
                         speech=f"Sure. {day[0].upper() + day[1:]} I have {self._list(slots)}. Which works best?")
        return Reply("What day works for you?", "ask_day", speech="Sure, I can check. What day works for you?")
