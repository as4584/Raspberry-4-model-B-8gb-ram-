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
from receptionist.dialogue import ANYTHING_ELSE, DEFAULT_HOURS, OPENERS, Dialogue, Reply
from receptionist.profile import answer as profile_answer
from receptionist.speech_format import spoken_time, spoken_when

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
        "human": re.compile(r"\b(someone|somebody|person|human|operator|representative|staff|manager|receptionist|real|alex|owner)\b"),
        "emergency": re.compile(r"\b(emergency|urgent|hurt|injur|bleed|pain|911|ambulance|right now)\w*"),
        "cancel": re.compile(r"\bcancel")}
BOOK_WORDS = re.compile(r"\b(book|appointment|schedule|reserve|come in|slot|opening|availability|available)\b")
SMALL_TALK = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening))?\W*(there\W*)?(how are you|how's it going|how are things)|^(hi|hello|hey)( there)?$")
GOODBYE = re.compile(r"\b(that'?s everything|that'?s it for now|nothing else|i'?m all set|talk (to you )?soon|take care|see ya|see you (later|soon)|bye|goodbye|thanks,? by|thank you,? by|that's all|that is all|that'll be all|have a (good|nice|great) day|bye bye|god bless|(i )?(gotta|got to|have to|need to) (go|run|hop off|jump)|gotta run|i'?ll call (you )?back|i'?ll try (you )?(again )?later)\b")


def closing(low: str) -> bool:
    """A goodbye that ends what they said. A "bye" to someone in the room, followed by
    more talking, hung up on a real call."""
    found = list(GOODBYE.finditer(low))
    return bool(found) and (len(low.split()) <= 6 or len(low[found[-1].end():].split()) <= 3)


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
DATE_Q = re.compile(r"\bwhat('?s| is)? (the )?(day|date|time)( is it| today)?\b|\btoday'?s date\b|\bwhat day is (it|today)\b")
UNKNOWN_MATCH = 0.6           # an unplaced question takes a checked answer only when it's a fair match
STRONG_MATCH = 0.75          # override a keyword FAQ hit only when clearly closer (Pi: good 0.85+, wrong 0.61)
# A question ABOUT the receptionist ("am I talking to a real person?") is not a transfer request.
IDENTITY_Q = re.compile(r"\b(am i (talking|speaking) (to|with)|are you|is (this|it|that)|was that)\b.*\b(real|person|human|robot|bot|machine|computer|ai|a i|recording)\b")
AVAIL_Q = re.compile(r"\bwhat (times|days|slots) do you (have|guys have)\b|\bwhen can i come in\b|\bwhen (are|is) (you|alex|someone|anyone|somebody)( guys)? (free|available|around)\b|\bare you (free|available) (on|at|this|next|tomorrow)\b")
TALK_TO = re.compile(r"\b(speak|talk) (with|to) (alex|the owner|someone|somebody|anyone|a person|a human|a real person)\b|\b(can|could) i (get|reach) (alex|the owner)\b")
REAL_PERSON = ("I'm LexMakesIt's A I receptionist. Alex built me, and I can answer questions or book you a free call with Alex.")
LOST_SPEECH = "I'm sorry, I didn't quite follow all of that. What would you like help with today?"
# Never "outside what I can help with": an honest "I don't want to guess" plus a real next step.
UNKNOWN_SPEECH = ("That's a good question, and I don't want to guess. I can book you a free call with Alex "
                  "to go over it, or take a message for him. Which would you prefer?")
NOT_FOLLOW_SPEECH = ("Sorry, I'm not sure I follow. I can tell you about LexMakesIt's websites and prices, "
                     "or book you a free call with Alex. What would help?")
QUESTION = re.compile(r"^(so |and |but |wait |also |hey |um |uh )*(can|could|do|does|did|what|what's|how|is|are|will|would|"
                      r"where|who|why|when|which|any|have)\b|\?\s*$")
# Six simulated callers, 2026-11-26 (tests/test_kindness.py).
MESSAGE = re.compile(r"\b(take|leave|pass( on| along)?) (a |him a |alex a )?message\b|\b(have|ask|get) (him|alex|someone|somebody) (to )?call\b"
                     r"|\b(call|get back to) me( back)?\b|\bcall ?back\b|\bcallback\b")
WAIT = re.compile(r"^(hold on|hang on|one (sec|second|moment|minute)|give me (a|one) (sec|second|minute|moment)|"
                  r"just a (sec|second|moment|minute)|wait a (sec|second|minute))\b")
REAL_EMERGENCY = re.compile(r"\b(fire|ambulance|bleeding|hurt|injured|heart attack|can'?t breathe|police|overdose|gas leak|unconscious)\b")
SITE_TROUBLE = re.compile(r"\b(site|website|page|email)\b.*\b((is|'s|went|has been|keeps|got|been)( going)? (down|broken|offline|hacked)|crashed|(isn'?t|is not|not|stopped|quit) working)\b")
HYPOTHETICAL = re.compile(r"\b(what if|if|in case|whenever|ever|would|could|what happens)\b")
NO_BOOK = re.compile(r"\b(don'?t|do not|not) (want|need|ready) to (book|schedule)|\bnot (ready|yet)\b.*\b(book|schedul)|"
                     r"\bjust (want|wanted|need|needed) (to know|the price|a price|some info|information)|\bnot booking\b")
READBACK = re.compile(r"\b(read|say|repeat|give)\b.*\b(number|phone|name)\b.*\bback\b|\bread (it|that|that number|my number) back\b|"
                      r"\bwhat (number|name) did you (get|put|write|take)\b|\b(what'?s|what is|what was) (my|the) (booking )?code\b|"
                      r"\b(repeat|say|give me) (the|my) (booking )?code\b|\bcode again\b")
PRICE_SHAPE = re.compile(r"\b(onetime|one[- ]?time|once|one off|monthly|every month|per month|a month|each month|up ?front)\b.*\b(or|then|and|plus)\b|\b(is it|is that|is the)\b.*\b(onetime|monthly|every month)\b")
PRICE_SHAPE_SPEECH = ("The website itself is a one-time cost, starting at one thousand dollars. Then it's ten dollars a month "
                      "to keep it managed and running. Alex can give you an exact quote on a free call.")
MONTHLY_Q = re.compile(r"\b(ten|10)( dollars| bucks)?( a| per)? month\b.*\b(cover|for|include|get)\b|\bwhat('?s| is| does) the (monthly|ten dollars?)\b")
MONTHLY_SPEECH = "The ten dollars a month covers managing your website after it's built. Alex can go over the details on a free call."
AI_Q = re.compile(r"\bwhat('?s| is| does)( an?)? (ai|a i|artificial intelligence)( mean)?\b")
AI_SPEECH = ("It just means I'm a computer program that can talk with you, a bit like a very helpful answering machine. "
             "Alex set me up so you can get answers any time. What can I help you with?")
SAID_NAME = re.compile(r"\b(?:my name is|my name'?s|name'?s|this is|it'?s|i'?m|i am|call me)\s+(.*)", re.I)
ORDINAL_PICK = r"(^(the |um |uh |let'?s do |i'?ll take |how about |ok(ay)? )*(the )?{w}( one| slot| option| time)?$|\b{w} (one|slot|option)\b|\bthe {w}\b(?! (thing|of|time i))|\b{w} works\b)"
DISTRESS = re.compile(r"\b(drowning|overwhelmed|stressed|stressful|struggling|burned|burnt|ripped off|scammed|vanished|ghosted|"
                      r"took my money|lost (my |all my )?money|sales are (way |really )?down|can'?t afford|frustrat\w*|upset|worried|"
                      r"scared|nervous|don'?t know where to start|hard time|tough time|rough time|losing customers)\b")
BURNED = re.compile(r"\b(burned|burnt|ripped off|scammed|vanished|ghosted|took my money|took \w+ (thousand|hundred) dollars)\b")
MONEY_WORRY = re.compile(r"\b(can'?t afford|money('?s| is) tight|on a budget|lost (my |all my )?money)\b")
SPANISH = re.compile(r"\b(spanish|espa[nñ]ol|habla)\b")
SPANISH_SPEECH = ("I'm sorry, I can only speak English right now. I'll keep it simple, "
                  "and I can take a message for Alex if that's easier.")
WEATHER = re.compile(r"\b(cold|hot|freezing|rain\w*|snow\w*|weather|humid|chilly)\b.*\b(today|out|outside|isn'?t it|huh)\b")
REPEAT_ASK = re.compile(r"\b(what was that|say that again|repeat that|come again|pardon|one more time|again,? slowly|"
                        r"didn'?t catch that|what did you say|could you repeat)\b")
# "Did you cancel it?" / "is it still booked?" is a question, never a cancel.
STATUS_Q = re.compile(r"\b(did you|was it|is it|has it been|have you) (cancel|cancelled|canceled)\b|\bstill (there|booked|on|good|scheduled|happening)\b")
URGENT = re.compile(r"\burgent\b|\basap\b|\bas soon as possible\b|\bright away\b|\btoday\b.*\bcall\b|\bcall\b.*\btoday\b")
WHEN_CALL = re.compile(r"\b(will|when will|is|when is|can|could) (he|alex) (call|get back|be calling|reach)\b|\bpromise\b")
MOVE = re.compile(r"\b(move|reschedule|change|switch|push)\b.*\b(it|that|appointment|booking|call)\b|\binstead\b")
CANCEL_IT = re.compile(r"\bcancel (it|that|this|my (appointment|booking|call)|the (appointment|booking|call))\b|^cancel$")
NAME_LEAD = re.compile(r"^((hi|hey|yeah|yes|oh|sure|okay|ok|well|um|uh|so)\W+)*(it'?s|this is|my name is|name'?s|i'?m|i am|call me)?\s*", re.I)
DIGIT_WORDS = {"zero": "0", "oh": "0", "o": "0", "one": "1", "two": "2", "three": "3", "four": "4",
               "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9"}
TALK_TO_SPEECH = ("Alex isn't available right this second, but I can take a message and have him call you back, "
                  "or book you a free call with him. Which would you prefer?")
NOT_A_NAME = {"yes", "yeah", "no", "nope", "sure", "okay", "ok", "hello", "hi", "um", "uh", "the", "a", "actually", "wait",
              "si", "sí", "bueno", "mira", "hola", "for", "about", "our", "we", "he", "she", "they", "calling", "here", "speaking",
              "can", "could", "would", "will", "do", "does", "did", "how", "what", "when", "where", "why", "who", "is", "are",
              "cancel", "move", "book", "thanks", "thank", "never", "i", "it", "that", "this", "just", "hold", "hang",
              "hmm", "and", "but", "not", "sorry", "please", "great", "perfect", "alright", "cool", "good"}


def spoken_number(digits: str) -> str:
    """'9085501422' -> 'nine oh eight, five five oh, one four two two' (how people read numbers back)."""
    w = ["oh", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    d = digits[1:] if len(digits) == 11 and digits[0] == "1" else digits
    groups = [d[:3], d[3:6], d[6:]] if len(d) == 10 else [d]
    return ", ".join(" ".join(w[int(c)] for c in g) for g in groups if g)


def spoken_digits(text: str) -> str:
    return "".join(t if t.isdigit() else DIGIT_WORDS.get(t, "") for t in re.findall(r"[a-z]+|\d", text.lower()))


DAYTIME = (r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|today|tomorrow|morning|afternoon|evening|"
           r"\d{1,2}(?::\d\d)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|noon)"
           r"(?:\s*(?:o'?\s?clock|a\.? ?m\.?|p\.? ?m\.?|in the (?:morning|afternoon|evening)))?")
_SELF_FIX = re.compile(rf"\b{DAYTIME}\W+(?:(?:no|nope|wait|sorry|actually|i mean|scratch that)\W+)+(?={DAYTIME}\b)", re.I)
_NOT_THAT = re.compile(rf"\bnot (?:at |on )?{DAYTIME}\W*", re.I)


def corrected(text: str) -> str:
    """'Tuesday, no, no, Wednesday' -> 'Wednesday'; 'not ten, two o'clock' -> 'two o'clock'."""
    return _NOT_THAT.sub("", _SELF_FIX.sub("", text)).strip()
# Thinking noises and speech-to-text noise tags: a person waits, they don't answer them.
FILLER = re.compile(r"^\W*((um+|uh+|hmm+|so|you know|whoa|like|well|[\[(][^\])]*[\])])\W*)+$")
# A leading "okay, like ..." / "so ..." is a discourse marker, not an answer.
LEAD_IN = re.compile(r"^(okay|ok|alright|so|well|um|uh)\b[\s,]*(so\s+|like\s+|um\s+|uh\s+)*")
# A vague period ("next week") still needs a day; otherwise offer the soonest times.
VAGUE_PERIOD = re.compile(r"\b(next week|this week|sometime|some time|later|weekend)\b")
REPHRASE = {"ask_again_time": "Sorry, I missed the time. You can say something like ten a m.",
            "ask_again_day": "Sorry, I didn't catch the day. Something like tomorrow, or Thursday?",
            "confirm_again": "Sorry, I just need a yes or a no. Should I book it?",
            "repeat": "Sorry, it's a little hard to hear. Could you say that one more time?",
            "presence": "Yep, still here. What can I help you with?"}
PRESENCE = re.compile(r"\b(are you (still )?there|can you hear me|you still there|is anyone there)\b")
PRESENCE_REPLY = "Yes, I'm here! How can I help?"
OWN_BUSINESS = re.compile(r"\bi (have|own|run|operate|started|just opened|opened) (a|an|my)\b.*\b(business|shop|store|salon|"
                          r"restaurant|bakery|barbershop|company|place|cafe|gym|studio|agency|clinic|practice|truck)\b")
PITCH = ("Nice! For a business like yours, we can set up a website, online booking, and automations like "
         "missed call text back. Want to book a free call with Alex to talk it over?")
FILLER_REPLIES = ("Take your time.", "Mm-hm.")
CONFIRM_TAIL = re.compile(r"\b(yes|yeah|yep|yes please|please book it|book it|that works|sounds? good)\s*(please|thank you|thanks)?\s*$")
LOCATION = re.compile(r"\b(where|address|located|location|directions|parking)\b")
OPEN_DAY = re.compile(r"\b(are you|you guys|is it|is the office)\s+open\b|\bopen on\b")


@dataclass
class When:
    date: date | None = None
    time: time | None = None
    part: str | None = None


def split_first(text: str) -> str:
    """The first sentence of a reply."""
    m = re.match(r"(.+?[.!?])(\s|$)", text)
    return m.group(1) if m else text


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
                 hours=None, profile: dict | None = None, require_caller_id: bool = False, clock=None, knowledge=None,
                 seed: int | None = None, on_message=None):
        self.store, self.intents = store, intents
        self.tz = ZoneInfo(timezone_name)
        self.profile = profile or {}
        self.hours = hours if hours is not None else (self.profile.get('hours_parsed') or DEFAULT_HOURS)
        self.clock = clock or (lambda: datetime.now(self.tz))
        self._last: dict[str, str] = {}
        self._last_speech: dict[str, str | None] = {}
        self._fillers: dict[str, int] = {}
        self.knowledge = knowledge
        self.on_message = on_message              # called with {name, callback}, never the words
        self.dialogue = Dialogue(store, timezone_name=timezone_name, hours=self.hours,
                                 require_caller_id=require_caller_id, clock=self.clock)
        self._calls: dict[str, _Call] = {}
        self._msg: dict[str, dict] = {}           # message being taken: step, name, callback, body
        self._booked: dict[str, str] = {}         # this call's booking reference (move/cancel "it")
        self._names: dict[str, str] = {}          # the caller's name, once given
        self._felt: dict[str, int] = {}           # times we've acknowledged feelings this call
        self._moving: dict[str, bool] = {}        # caller asked to move this call's booking
        self._utter: dict[str, str] = {}
        self._callbacks: dict[str, str] = {}
        self._last_msg: dict[str, int] = {}      # number given for a callback (read back on request)          # the caller's previous words (a repeat vs a new question)
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
        return Reply("That's outside what I can help with, but I can answer questions about LexMakesIt or book you a free call with Alex.", "unknown",
                     speech=UNKNOWN_SPEECH)

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
        s, low = session_id, " ".join(re.sub(r"[.!?,]+", " ", utterance.lower()).split())
        r = self._handle(utterance, session_id, caller_id)
        # Mid-booking, a SHORT reply we can't place is almost always a misheard answer ("Ten." -> "Pen"):
        # a person asks again. A real question ("do you do payment plans?") gets a real answer.
        last = self._last.get(session_id)
        short = len(low.split()) <= 3
        if r.action == "unknown" and short and last in ("offer_slots", "ask_which"):
            r = Reply("Sorry, which time was that?", "ask_again", speech="Sorry, which time was that?")
            last = "ask_again_time"
        elif r.action == "unknown" and short and last == "ask_day":
            r = Reply("Sorry, which day was that?", "ask_again", speech="Sorry, which day was that?")
            last = "ask_again_day"
        else:
            if r.action in ("unknown", "faq") and self.knowledge is not None:
                hit = self.knowledge.best(" ".join(utterance.split()))
                # A keyword answer ("website" -> contact) loses to a close checked answer; an unknown
                # takes a fair match only ("see examples of your work" is not the reviews answer).
                if hit and hit[2] >= (UNKNOWN_MATCH if r.action == "unknown" else STRONG_MATCH):
                    r = Reply(hit[1], "knowledge", speech=hit[1])
            if r.action == "unknown" and len(utterance.split()) > 15:
                r = Reply(LOST_SPEECH, "lost", speech=LOST_SPEECH)
            elif r.action == "unknown" and not QUESTION.search(low):
                # Not a question (side talk, a garbled line): don't send it to Alex.
                r = Reply(NOT_FOLLOW_SPEECH, "unknown", speech=NOT_FOLLOW_SPEECH)
            last = r.action
        self._last[session_id] = last
        if r.action in ("booked", "rescheduled") and r.committed_id:
            self._booked[s] = r.committed_id
            self._moving.pop(s, None)
            if r.action == "booked" and s not in self._names:
                # Alex needs to know who he's calling (every simulated caller noticed it never asked).
                body = (r.speech or r.text).replace(ANYTHING_ELSE, "").strip()
                self._msg[s] = {"step": "booking_name", "ref": r.committed_id}
                r = Reply(r.text, r.action, r.committed_id,
                          speech=f"{body} Can I get your name, so Alex knows who he's talking to?")
        r = self._vary(r)
        if DISTRESS.search(low) and r.action != "goodbye":
            r = self._acknowledge(r, low, s)
        if r.speech and r.speech == self._last_speech.get(session_id):
            # People rephrase when they repeat themselves; identical twice sounds like a recording.
            again = REPHRASE.get(last) or REPHRASE.get(r.action)
            if again is None and r.action == "unknown":
                again = ("I'm not sure on that one either, I'm sorry. Alex would know, though. "
                         "Should I take a message for him, or book you a free call?")
            elif again is None and low != self._utter.get(s) and r.action in ("faq", "knowledge", "pitch"):
                # A different question that lands on the same answer: that answer doesn't fit.
                r, again = Reply(UNKNOWN_SPEECH, "unknown", speech=UNKNOWN_SPEECH), UNKNOWN_SPEECH
            elif again is None:
                body = r.speech
                first = split_first(body)
                if first in OPENERS and first != body:
                    body = body[len(first):].strip()
                again = f"Sure, once more. {body}"
            r = Reply(r.text, r.action, r.committed_id, speech=again)
        self._last_speech[session_id] = r.speech
        self._utter[s] = low
        return r

    def _acknowledge(self, r: Reply, low: str, s: str) -> Reply:
        """Feelings first: 'I'm drowning' got 'Nice! For a business like yours...'."""
        n = self._felt.get(s, 0)
        self._felt[s] = n + 1
        if n:
            ack = "I hear you."
        elif BURNED.search(low):
            ack = "Oh no, I'm really sorry that happened to you. That's awful, and it makes sense to be careful."
        elif MONEY_WORRY.search(low):
            ack = "I completely understand. Money is tight for a lot of small businesses right now."
        else:
            ack = "I'm really sorry, that sounds really stressful."
        body = r.speech or r.text
        if r.action in ("unknown", "lost", "pitch"):
            body = ("Alex helps small businesses with exactly this, and the first call is free. "
                    "I can book you one, or take a message so he can call you back. Which would be better?")
        first = split_first(body)
        if first in OPENERS and first != body:
            body = body[len(first):].strip()
        return Reply(r.text, r.action, r.committed_id, speech=f"{ack} {body}")

    # ---- messages --------------------------------------------------------------------
    @staticmethod
    def _name_from(text: str) -> str | None:
        m = SAID_NAME.search(text)
        if not m and len(text.split()) > 6:
            return None                       # names come short; a long answer is something else
        src = m.group(1) if m else NAME_LEAD.sub("", text.strip())
        first = (src.split() or [""])[0].strip(",.")
        if m and (first.lower() in NOT_A_NAME or not first[:1].isupper()):
            # "Dorothy, I'm calling about..." / "I'm panicking": the name isn't after "I'm".
            src = text[:m.start()]
        words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'-]*", src)
        dedup = [w for i, w in enumerate(words) if i == 0 or w.lower() != words[i - 1].lower()]
        if not dedup or dedup[0].lower() in NOT_A_NAME:
            return None
        name = [dedup[0]]
        if len(dedup) > 1 and len(dedup) <= 3 and dedup[1][0].isupper() and not dedup[1].isupper() \
                and "'" not in dedup[1] and dedup[1].lower() not in NOT_A_NAME | {"my", "i", "and", "i'm"}:
            name.append(dedup[1])
        return " ".join(w[0].upper() + w[1:] for w in name)

    def _start_message(self, s: str, caller_id: str | None, lead: str, body: str | None = None) -> Reply:
        flow = self._msg[s] = {"step": "name", "body": body}
        self._calls[s] = _Call()
        if s in self._names:
            flow["name"] = self._names[s]
            return self._ask_number(flow, caller_id, lead)
        return Reply(f"{lead} What's your name?", "ask_name", speech=f"{lead} What's your name?")

    def _ask_number(self, flow: dict, caller_id: str | None, lead: str | None = None) -> Reply:
        flow["step"] = "number"
        head = lead or f"Thanks, {flow['name']}."
        q = "Is the number you're calling from the best one to reach you?" if caller_id else \
            "What's the best number for Alex to call you back on?"
        return Reply(f"{head} {q}", "ask_number", speech=f"{head} {q}")

    def _message_step(self, flow: dict, text: str, low: str, s: str, caller_id: str | None) -> Reply | None:
        step = flow["step"]
        if step == "booking_name":
            self._msg.pop(s, None)
            name = self._name_from(text) if len(low.split()) <= 5 else None
            if not name:
                return None                       # they moved on; handle the turn normally
            self._names[s] = name
            self.store.set_name(flow["ref"], name, caller_id)
            words = f"Thanks, {name}! {ANYTHING_ELSE}"
            return Reply(words, "name_taken", speech=words)
        if step == "name":
            name = self._name_from(text)
            if not name and (QUESTION.search(low) or BOOK_WORDS.search(low)):
                self._msg.pop(s, None)            # they asked something else: answer that instead
                return None
            if not name:
                return Reply("Sorry, what was your name?", "ask_name", speech="Sorry, what was your name?")
            flow["name"] = self._names[s] = name
            return self._ask_number(flow, caller_id)
        if step == "number":
            digits = spoken_digits(text)
            heard_back = None
            if len(digits) == 10 or (len(digits) == 11 and digits[0] == "1"):
                flow["callback"] = digits
                heard_back = spoken_number(digits)
            elif len(digits) >= 3 and flow.get("tries", 0) >= 1 and caller_id:
                # Phone-quality speech drops digits; after two tries, don't loop the caller.
                flow["callback"] = caller_id
                flow["step"] = "body"
                self._callbacks[s] = caller_id
                words = ("No worries, I'll save the number you're calling from, and Alex can reach you there. "
                         "What would you like me to tell him?")
                if flow.get("body"):
                    return self._save_message(flow, s, caller_id)
                return Reply(words, "ask_message", speech=words)
            elif len(digits) >= 3:
                flow["tries"] = flow.get("tries", 0) + 1
                words = "Sorry, I think I missed a digit. Could you say the whole number again, slowly?"
                return Reply(words, "ask_number", speech=words)
            elif caller_id and (AFFIRM.match(low) or re.search(r"\b(this|that|same) (number|one)\b", low)):
                flow["callback"] = caller_id
            elif QUESTION.search(low):
                self._msg.pop(s, None)
                return None
            else:
                words = "No problem. What's the best number for Alex to call?"
                return Reply(words, "ask_number", speech=words)
            if flow.get("body"):
                self._callbacks[s] = flow["callback"]
                return self._save_message(flow, s, caller_id, heard_back)
            flow["step"] = "body"
            self._callbacks[s] = flow["callback"]
            words = f"Got it, {heard_back}. And what would you like me to tell Alex?" if heard_back else \
                "Got it. And what would you like me to tell Alex?"
            return Reply(words, "ask_message", speech=words)
        if READBACK.search(low):
            r = self._read_back(s, caller_id)     # still taking the message: ask for it after
            words = r.speech.replace("Is that right?", "And what would you like me to tell Alex?")
            return Reply(words, "read_back", speech=words)
        flow["body"] = text.strip()
        return self._save_message(flow, s, caller_id)

    def _read_back(self, s: str, caller_id: str | None) -> Reply:
        """'Read my number back' / 'what's my booking code?': say what we have, slowly."""
        parts = []
        if s in self._names:
            parts.append(f"I have your name as {self._names[s]}")
        number = self._callbacks.get(s) or caller_id
        digits = re.sub(r"\D", "", number or "")
        if len(digits) >= 10:
            parts.append(f"your number as {spoken_number(digits)}")
        code = None
        if self._booked.get(s):
            row = next((b for b in self.store.active() if b["id"] == self._booked[s]), None)
            code = row and row.get("code")
        if code:
            parts.append("your booking code as " + " ".join(spoken_number(c) for c in code))
        if not parts:
            words = "I don't have any details from you yet. Would you like to leave a message for Alex?"
            return Reply(words, "read_back", speech=words)
        text = ", and ".join(parts)
        words = text[0].upper() + text[1:] + ". Is that right?"
        return Reply(words, "read_back", speech=words)

    def _save_message(self, flow: dict, s: str, caller_id: str | None, heard_back: str | None = None) -> Reply:
        self._msg.pop(s, None)
        self._last_msg[s] = self.store.add_message(flow.get("name"), flow.get("callback"), flow["body"], caller_id=caller_id)
        if URGENT.search(flow["body"].lower()):
            self.store.mark_message(self._last_msg[s], "urgent")
        if self.on_message:
            try:
                self.on_message({"name": flow.get("name"), "callback": flow.get("callback")})
            except Exception:
                pass                              # a notifier problem never breaks the call
        at = f" at {heard_back}" if heard_back else ""
        words = (f"Got it, {flow.get('name') or 'thanks'}. I'll make sure Alex gets your message, and he'll call you "
                 f"back{at} as soon as he can. {ANYTHING_ELSE}")
        return Reply(words, "message_taken", speech=words)

    def _book(self, when: str, session_id: str, caller_id: str | None = None) -> Reply:
        ref = self._booked.get(session_id)
        if ref and self._moving.get(session_id):
            r = self.dialogue.handle(f"reschedule {ref} {when}", session_id, caller_id=caller_id)
            if r.action == "confirm_required":
                starts = datetime.strptime(when, "%Y-%m-%d %H:%M").replace(tzinfo=self.tz)
                words = f"Sure, I can move it to {spoken_when(starts, self.tz)}. Should I go ahead?"
                return Reply(r.text, r.action, r.committed_id, speech=words)
            return r
        return self.dialogue.handle(f"book {when}", session_id, caller_id=caller_id)

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
        text = corrected(text)          # 'Tuesday, no, no, Wednesday' -> 'Wednesday'
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
        flow = self._msg.get(session_id)
        if flow and not closing(low):
            r = self._message_step(flow, text, low, session_id, caller_id)
            if r is not None:
                return r
        ref = self._booked.get(session_id)
        if ref and STATUS_Q.search(low):
            row = next((b for b in self.store.active() if b["id"] == ref), None)
            if row:
                when = spoken_when(datetime.fromisoformat(row["starts"]), self.tz)
                words = f"Yes, you're still booked for {when}. Nothing's been cancelled. Would you like to keep it?"
            else:
                words = "That one isn't on the calendar anymore. Would you like me to book a new time?"
            return Reply(words, "status", speech=words)
        if ref and CANCEL_IT.search(low):
            # Before the goodbye check: "just cancel it entirely, thanks bye" must cancel.
            self._calls[session_id] = _Call()
            if self.store.cancel(ref, caller_id=caller_id):
                self._booked.pop(session_id, None)
                words = f"Okay, it's cancelled. {ANYTHING_ELSE}"
                return Reply("Your appointment is cancelled.", "cancelled", speech=words)
        m = SAID_NAME.search(text)
        if m and re.match(r"^(?:my name is|my name'?s|this is)", m.group(0), re.I) and session_id not in self._names:
            got = self._name_from(m.group(0))
            if got:
                self._names[session_id] = got     # "this is Jordan" said up front: don't ask again later
        if closing(low) and not (pending and AFFIRM.match(low)):
            # "I gotta go" / "No thanks. Bye." / "God bless, goodbye": let them go, warmly.
            self._calls[session_id] = _Call()
            self._msg.pop(session_id, None)
            name = self._names.get(session_id)
            words = f"No problem{', ' + name if name else ''}. Thanks for calling, and take care!"
            if "god bless" in low:
                words = f"God bless you too{', ' + name if name else ''}. Thanks for calling, and take care!"
            return Reply("Thanks for calling. Have a great day!", "goodbye", speech=words)
        if WAIT.match(low) and (len(low.split()) <= 5 or not QUESTION.search(WAIT.sub("", low).strip())) \
                and not any(vars(parse_when(re.sub(r"\b(one|a|1) (sec|second|moment|minute)\b", "", WAIT.sub("", low)), self.clock())).values()):
            return Reply("Take your time.", "filler", speech="Sure, take your time.")
        if READBACK.search(low):
            return self._read_back(session_id, caller_id)
        fixed = spoken_digits(text)
        if self._last.get(session_id) in ("read_back", "message_taken") and (len(fixed) == 10 or (len(fixed) == 11 and fixed[0] == "1")):
            self._callbacks[session_id] = fixed
            if self._last_msg.get(session_id):
                self.store.set_message_callback(self._last_msg[session_id], fixed)
            words = f"Thanks for catching that. I've got {spoken_number(fixed)}. {ANYTHING_ELSE}"
            return Reply(words, "number_fixed", speech=words)
        if PRICE_SHAPE.search(low) and not re.search(r"\b(deposit|payment plans?|installments?|split)\b", low):
            return Reply(PRICE_SHAPE_SPEECH, "knowledge", speech=PRICE_SHAPE_SPEECH)
        if MONTHLY_Q.search(low):
            return Reply(MONTHLY_SPEECH, "knowledge", speech=MONTHLY_SPEECH)
        if AI_Q.search(low):
            return Reply(AI_SPEECH, "knowledge", speech=AI_SPEECH)
        if REPEAT_ASK.search(low) and self._last_speech.get(session_id):
            body = self._last_speech[session_id]
            first = split_first(body)
            if first in OPENERS and first != body:
                body = body[len(first):].strip()
            return Reply(body, "repeat_back", speech=f"Of course. {body}")
        if SPANISH.search(low):
            return Reply(SPANISH_SPEECH, "spanish", speech=SPANISH_SPEECH)
        if WEATHER.search(low) and not BOOK_WORDS.search(low):
            return Reply("I hear you! What can I help you with today?", "small_talk",
                         speech="Oh, I hear you! What can I help you with today?")
        if SITE_TROUBLE.search(low) and not REAL_EMERGENCY.search(low) and not HYPOTHETICAL.search(low):
            return self._start_message(session_id, caller_id, body=text,
                                       lead="Oh no, I'm sorry about that. Let me take a message so Alex can get on it as soon as he can.")
        last = self._last.get(session_id)
        if (WHEN_CALL.search(low) or URGENT.search(low)) and self._last.get(session_id) == "message_taken":
            # Honest: no promised time. Urgent marks the message so Alex sees it first.
            if URGENT.search(low) and self._last_msg.get(session_id):
                self.store.mark_message(self._last_msg[session_id], "urgent")
            words = ("I've marked it urgent so Alex sees it first. I can't promise an exact time, but he'll call you "
                     "back as soon as he can.") if URGENT.search(low) else                     ("I can't promise an exact time, but Alex gets your message right away and calls back as soon as he "
                     "can. Want me to mark it urgent?")
            return Reply(words, "message_followup", speech=words)
        if MESSAGE.search(low) or (last in ("unknown", "handoff", "spanish") and re.search(r"\bmessage\b", low)) \
                or (last == "handoff" and AFFIRM.match(low)):
            return self._start_message(session_id, caller_id, lead="Of course, I can take a message for Alex.")
        if ref and MOVE.search(low):
            self._moving[session_id] = True
        if NO_BOOK.search(low) and (pending or st.offered or st.date):
            # "I just wanted to know the price first": stop offering times and answer.
            st = self._calls[session_id] = _Call()
            self.dialogue._pending.pop(session_id, None)
            pending = False
            text = re.sub(r"\b(book|booking|appointment|schedule)\w*\b", "", text, flags=re.I)
            low = re.sub(r"\b(book|booking|appointment|schedule)\w*\b", "", low)
            if not QUESTION.search(re.sub(NO_BOOK, "", low)) and not re.search(r"\b(price|cost|how much)\b", low):
                words = "No problem at all, there's no rush. What would you like to know?"
                return Reply(words, "reply", speech=words)
        # "one time or monthly" is a price question, not one o'clock.
        text = re.sub(r"\bone[- ]?time\b", "onetime", text, flags=re.I)
        low = re.sub(r"\bone[- ]?time\b", "onetime", low)
        if FILLER.match(low) or (not pending and re.fullmatch(r"(okay|ok)\W*(um+|uh+)\W*", low)):
            n = self._fillers.get(session_id, 0); self._fillers[session_id] = n + 1
            words = FILLER_REPLIES[n % 2]
            return Reply(words, "filler", speech=words)
        self._fillers[session_id] = 0
        if PRESENCE.search(low):
            return Reply(PRESENCE_REPLY, "presence", speech=PRESENCE_REPLY)
        offered_call = "free call" in (self._last_speech.get(session_id) or "")
        if offered_call and not pending and (AFFIRM.match(low) or re.search(r"\b(let'?s|sounds good|sure)\b", low)) \
                and not any(vars(parse_when(text, self.clock())).values()):
            # "Yeah, let's go over that" right after offering a free call: start booking it.
            self._last_speech[session_id] = None
            return self._handle("what times do you have", session_id, caller_id)
        st0 = self._calls.get(session_id)
        if OWN_BUSINESS.search(low) and not pending and not (st0 and st0.offered):
            return Reply(PITCH, "pitch", speech=PITCH)
        stripped = LEAD_IN.sub("", low).strip()
        if stripped != low and len(stripped.split()) >= 3:
            low, text = stripped, LEAD_IN.sub("", text.lower()).strip()
        if DATE_Q.search(low) and not parse_when(text, self.clock()).time:
            now = self.clock()
            day, _, clock = spoken_when(now).partition(" at ")
            return Reply(f"It's {day}, and it's {clock}.", "date",
                         speech=f"It's {day}, and it's about {clock}. What can I help you with?")
        if pending and AFFIRM.match(low):
            return self.dialogue.handle("yes", session_id, caller_id=caller_id)
        if IDENTITY_Q.search(low):
            words = self.knowledge.answer("real_person") if self.knowledge is not None else None
            return Reply(words or REAL_PERSON, "knowledge", speech=words or REAL_PERSON)
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

        if closing(low):
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
                if re.search(ORDINAL_PICK.format(w=word), low):
                    pick = st.offered[i]
                    return self._book(f"{pick:%Y-%m-%d %H:%M}", session_id, caller_id=caller_id)
        bare = AFFIRM.match(low) and not pending and not any(vars(parse_when(text, now)).values())
        if bare and st.date and st.time:          # "yes" to a single time we just proposed
            return self._book(f"{datetime.combine(st.date, st.time):%Y-%m-%d %H:%M}", session_id,
                                        caller_id=caller_id)
        if bare:
            return Reply("Great! What can I help you with?", "reply", speech="Okay. What can I help you with?")

        intent, conf = self.intents.classify(text)
        if AVAIL_Q.search(low):                   # "when are you free?" is a scheduling question
            intent, conf = "availability", max(conf, 0.9)
        elif TALK_TO.search(low) and not IDENTITY_Q.search(low) \
                and not re.search(r"\b(am i|are you|is this)\b.*\b(human|person|real|bot|robot|ai)\b", low):
            intent, conf = "human", max(conf, 0.9)
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
                    return self._book(f"{datetime.combine(st.date, st.time):%Y-%m-%d %H:%M}",
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
            if intent == "goodbye" and (closing(low) or len(low.split()) <= 6):
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
            if intent == "emergency" and REAL_EMERGENCY.search(low):
                return Reply("If this is an emergency, please hang up and dial 911.", "handoff",
                             speech="Okay. If this is an emergency, please hang up and dial nine one one.")
            if intent == "emergency":
                # "My site is down, it's an emergency" is urgent business, not a 911 call.
                return self._start_message(session_id, caller_id, body=text,
                                           lead="I'm sorry you're dealing with that. Let me take a message so Alex can get back to you as soon as he can.")
            # The direct line once, and a message as the easy option (simulated callers heard the
            # number five times a call and still couldn't leave a message).
            contact = (self.profile.get("faq") or {}).get("contact")
            if contact:
                words = f"Of course. {contact} Or if it's easier, I can take a message and have him call you back."
                return Reply(words, "handoff", speech=words)
            return Reply(TALK_TO_SPEECH, "handoff", speech=TALK_TO_SPEECH)
        if intent == "cancel" or low.startswith("cancel"):
            if re.search(r"\d", text) or any(w in low.split() for w in dr._UNITS):
                return self.dialogue.handle(low if low.startswith("cancel") else "cancel " + low, session_id,
                                            caller_id=caller_id)
            return Reply("What's your four digit booking code?", "ask_code",
                         speech="Sure. What's your four digit booking code?")
        booking_ask = intent in ("book", "availability", "reschedule") and BOOK_WORDS.search(low)
        if (intent in ("faq", "faq_unknown") or profile_answer(self.profile, text)[1]) and not has_when                 and not booking_ask:
            if intent == "faq_unknown" and not profile_answer(self.profile, text)[1]:
                return self._unknown()
            return self._faq(text)
        booking_turn = intent in ("book", "availability", "reschedule") and conf >= MIN_CONFIDENCE
        follow_up = has_when and (st.date or st.time or st.offered or pending)
        if not (booking_turn or follow_up or has_when):
            return Reply("That's outside what I can help with, but I can answer questions about LexMakesIt or book you a free call with Alex.", "unknown",
                         speech=UNKNOWN_SPEECH)

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
                r = self._book(f"{starts:%Y-%m-%d %H:%M}", session_id, caller_id=caller_id)
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
                    r = self._book(f"{datetime.combine(nxt, asked):%Y-%m-%d %H:%M}", session_id,
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
        if not VAGUE_PERIOD.search(low) and not st.date:
            day = now.date()
            for _ in range(8):
                slots = self._free_slots(day, st.part)
                if slots:
                    st.date, st.offered = day, slots
                    words = self._day_words(day, now.date())
                    return Reply(f"{words}: {self._list(slots)}", "offer_slots",
                                 speech=f"Sure. The soonest I have is {words} at {self._list(slots)}. "
                                        f"Or is there a day that works better?")
                nxt = self._next_open_day(day)
                if nxt is None:
                    break
                day = nxt
        return Reply("What day works for you?", "ask_day", speech="Sure, I can check. What day works for you?")

