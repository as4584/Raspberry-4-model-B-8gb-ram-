"""Rule-based NLU command normalizer — English and Spanish.

Converts natural-language utterances into the structured command vocabulary
that Dialogue.handle() understands. No LLM required; this is the front-door
layer that runs in front of the dialogue state machine.

When a real local NLU model (Qwen3-0.6B etc.) is integrated in S3/S3A it
will replace or supplement this layer. The structured output contract here
defines what that model must reproduce.

Output vocabulary:
  "book YYYY-MM-DD HH:MM [duration]"  — booking intent with ISO date/time
  "yes"                               — confirmation
  "no"                                — denial / cancellation of pending
  "human"                             — escalation request
  "help"                              — help / FAQ intent
  "cancel <reference>"                — cancel by opaque booking reference
  <passthrough>                       — unrecognized; Dialogue handles fallback
"""
from __future__ import annotations
import re

# ---------------------------------------------------------------------------
# 12-hour clock → 24-hour helper
# ---------------------------------------------------------------------------

_12H = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b",
    re.IGNORECASE,
)


def _to_24h(h: int, m: int, meridiem: str) -> str:
    meridiem = meridiem.lower()
    if meridiem == "am":
        hour = 0 if h == 12 else h
    else:
        hour = h if h == 12 else h + 12
    return f"{hour:02d}:{m:02d}"


def _normalise_clock(text: str) -> str:
    """Replace 12-hour time expressions with 24-hour HH:MM."""
    def replace(m: re.Match) -> str:
        h = int(m.group(1))
        mins = int(m.group(2)) if m.group(2) else 0
        return _to_24h(h, mins, m.group(3))
    return _12H.sub(replace, text)


# ---------------------------------------------------------------------------
# Date extraction helper
# ---------------------------------------------------------------------------

_DATE_TIME = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})(?:\s+(\d+))?")


def _pad_time(t: str) -> str:
    """Ensure HH:MM zero-pads single-digit hours."""
    h, m = t.split(":")
    return f"{int(h):02d}:{m}"


def _extract_booking(text: str) -> str | None:
    """Return 'book YYYY-MM-DD HH:MM [dur]' if a date+time is present."""
    t = _normalise_clock(text)
    # Strip connectors that sit between date and time
    t = re.sub(r"\s+\bat\b\s+", " ", t)         # English "at"
    t = re.sub(r"\s+\bel\b\s+", " ", t)         # Spanish "el"
    t = re.sub(r"\ba\s+las\s+", " ", t, flags=re.IGNORECASE)  # Spanish "a las"
    m = _DATE_TIME.search(t)
    if not m:
        return None
    date, time_, dur = m.group(1), _pad_time(m.group(2)), m.group(3)
    return f"book {date} {time_}" + (f" {dur}" if dur else "")


# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

_YES_EN = {
    "yes", "yeah", "yep", "yup", "sure", "sounds good", "that works",
    "confirm", "confirmed", "ok", "okay", "do it", "go ahead", "correct",
    "absolutely", "definitely", "of course", "please do", "please",
}
_YES_ES = {
    "sí", "si", "de acuerdo", "está bien", "esta bien", "claro",
    "claro que sí", "claro que si", "perfecto", "adelante", "confirmar",
    "confirmo", "sí confirmo", "si confirmo", "bueno", "por supuesto",
}

_NO_EN = {
    "no", "nope", "no thanks", "no thank you", "cancel that", "never mind",
    "forget it", "actually no", "not anymore", "disregard",
}
_NO_ES = {
    "no gracias", "no, gracias", "cancelar eso", "olvídalo", "olvidalo",
    "en realidad no", "no quiero", "ya no",
}

# First-token triggers — robust to trailing words/punctuation ("yes please",
# "okay!", "sure thing", "sí, por favor"). Deny is checked first so "no ..."
# never reads as an affirmation.
_AFFIRM_LEAD = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed",
    "correct", "absolutely", "definitely",
    "sí", "si", "claro", "perfecto", "adelante", "confirmar", "confirmo",
    "bueno", "vale",
}
_AFFIRM_PHRASES = (
    "sounds good", "that works", "of course", "please do", "will do",
    "de acuerdo", "está bien", "esta bien", "por supuesto",
)
_DENY_LEAD = {"no", "nope", "nah"}
_DENY_PHRASES = (
    "never mind", "cancel that", "forget it", "no thanks", "no thank",
    "not anymore", "disregard", "actually no",
    "no gracias", "cancelar eso", "olvídalo", "olvidalo", "en realidad no",
    "no quiero", "ya no",
)

_HUMAN_EN = re.compile(
    r"\b(human|person|agent|operator|representative|staff|"
    r"talk to someone|speak to|live person)\b",
    re.IGNORECASE,
)
_HUMAN_ES = re.compile(
    r"\b(humano|persona|agente|operador|hablar con|"
    r"alguien|un representante)\b",
    re.IGNORECASE,
)

_HELP_EN = re.compile(
    r"\b(help|how does this work|what can you do|instructions|"
    r"what do i say|commands)\b",
    re.IGNORECASE,
)
_HELP_ES = re.compile(
    r"\b(ayuda|ayúdame|ayudame|cómo funciona|como funciona|"
    r"necesito ayuda|qué puedes hacer|que puedes hacer)\b",
    re.IGNORECASE,
)

# Spanish booking trigger words
_BOOK_ES = re.compile(
    r"\b(reservar|reserva|agendar|agenda|"
    r"hacer una cita|hacer cita|"
    r"quisiera|quiero|me gustaría|me gustaria|"
    r"necesito una cita|programar)\b",
    re.IGNORECASE,
)

# Spanish cancel-by-reference
_CANCEL_ES = re.compile(
    r"^cancelar\s+([0-9a-f\-]{36})\s*$",
    re.IGNORECASE,
)

# Spanish "a las" time prefix — strip it so date extraction works
_A_LAS = re.compile(r"\ba\s+las\s+", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize(utterance: str) -> str:
    """Normalise a natural-language utterance to a Dialogue command string."""
    s = utterance.strip()
    low = s.lower()

    # --- Cancel by reference (English passthrough) ---
    if re.match(r"^cancel\s+[0-9a-f\-]{36}\s*$", low):
        return s

    # --- Cancel by reference (Spanish) ---
    m = _CANCEL_ES.match(s)
    if m:
        return f"cancel {m.group(1)}"

    # --- Exact structured passthrough ---
    if re.match(r"^book\s+\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(\s+\d+)?$", low):
        return s

    # Punctuation-tolerant forms for keyword matching
    bare = low.strip(" .,!?;:¡¿\"'")
    toks = re.findall(r"\w+", bare, re.UNICODE)
    first = toks[0] if toks else ""
    # When an explicit ISO date is present the utterance is a booking, even if
    # it opens with a filler affirmation ("ok so ... 2027-12-10 at 9am").
    has_iso = bool(re.search(r"\d{4}-\d{2}-\d{2}", s))

    # --- Denial (checked before affirm so "no ..." is never an affirmation) ---
    if bare in _NO_EN or bare in _NO_ES:
        return "no"
    if not has_iso:
        if first in _DENY_LEAD:
            return "no"
        if any(bare.startswith(p) for p in _DENY_PHRASES):
            return "no"

    # --- Confirmation ---
    if bare in _YES_EN or bare in _YES_ES:
        return "yes"
    if not has_iso:
        if first in _AFFIRM_LEAD:
            return "yes"
        if any(bare.startswith(p) for p in _AFFIRM_PHRASES):
            return "yes"

    # --- Human escalation ---
    if _HUMAN_EN.search(low) or _HUMAN_ES.search(low):
        return "human"

    # --- Help ---
    if _HELP_EN.search(low) or _HELP_ES.search(low):
        return "help"

    # --- Reference-less cancel: a real cancel reached the passthrough above
    #     (exact UUID) or was a deny keyword. Anything else starting with
    #     "cancel"/"cancelar" has no valid reference, so route to help rather
    #     than letting the dialogue attempt a cancel on arbitrary text. ---
    if low.startswith("cancel ") or low.startswith("cancelar "):
        return "help"

    # --- Spanish booking intent: strip trigger words, extract date/time ---
    if _BOOK_ES.search(low):
        cleaned = _BOOK_ES.sub("", s).strip()
        cleaned = _A_LAS.sub("", cleaned).strip()
        result = _extract_booking(cleaned)
        if result:
            return result

    # --- English natural booking phrases: strip lead-in, extract date/time ---
    _BOOK_EN_LEAD = re.compile(
        r"^(i'?d like to book|can you schedule( me)? for|"
        r"schedule me for|set up|appointment on|"
        r"make it|sign me up for|book me( in)? for|"
        r"book me in|book me at|book|i want|"
        r"i'?d like an? appointment)",
        re.IGNORECASE,
    )
    cleaned = _BOOK_EN_LEAD.sub("", s).strip()
    # Remove natural connectors
    cleaned = re.sub(r"^(for|on|at)\s+", "", cleaned, flags=re.IGNORECASE)
    result = _extract_booking(cleaned)
    if result:
        return result

    # --- Bare date+time (English or Spanish) ---
    result = _extract_booking(s)
    if result:
        return result

    # --- Unrecognized: return as-is for Dialogue fallback ---
    return s
