"""Factor-diverse synthetic caller utterances with oracle labels. Deterministic for (split, seed, n, mode).

Each example: sample a factor assignment -> build a semantic frame -> label = oracle(frame) ->
render text from the frame (template + slot phrases + correction + register + noise).
mode="diverse": greedy pairwise-coverage sampling over all factors.
mode="narrow":  random templates with clean, canonical phrasing (the "random-template" baseline).
"""
from __future__ import annotations

import hashlib
import random
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from lab.datagen import oracle, render
from lab.datagen.splits import families_for_split, normalize
from lab.datagen.templates import FAQ_ABOUT, TEMPLATES, UNKNOWN_ABOUT

TZ = ZoneInfo(oracle.TIMEZONE)
SOURCE_ID = "project_synthetic"
LICENSE = "project-authored"

INTENT_WEIGHTS = {"book": 20, "availability": 14, "reschedule": 14, "cancel": 9, "faq": 9,
                  "faq_unknown": 7, "human": 6, "emergency": 6, "injection": 15}
SLOT_FACTORS = {
    "date_style": ["iso", "month_day", "day_month", "weekday", "tomorrow", "day_after_tomorrow",
                   "in_n_days", "next_weekday", "missing"],
    "time_style": ["h12", "h12min", "h24", "words", "bare_ambiguous", "missing"],
    "duration_style": ["unspecified", "minutes", "words"],
    "edge": ["normal", "closed_day", "out_of_hours", "past", "dst"],
    "correction": ["none", "date", "time", "duration"],
}
COMMON_FACTORS = {
    "noise": ["clean", "fillers", "asr", "stutter", "typo"],
    "register": ["casual", "formal", "code_switch", "terse"],
}
REF_FACTOR = {"reference": ["present", "missing"]}
CONCRETE_DATE = {"iso", "month_day", "day_month", "weekday", "tomorrow", "day_after_tomorrow", "in_n_days"}
CONCRETE_TIME = {"h12", "h12min", "h24", "words"}
NARROW = {"date_style": ["iso", "month_day"], "time_style": ["h12"], "duration_style": ["unspecified", "minutes"],
          "edge": ["normal"], "correction": ["none"], "noise": ["clean"], "register": ["casual"],
          "reference": ["present"]}
DST_DATES = {"nonexistent": [date(2027, 3, 14), date(2028, 3, 12)], "ambiguous": [date(2027, 11, 7)]}
DST_TIME = {"nonexistent": "02:30", "ambiguous": "01:30"}


def factor_space(intent: str) -> dict[str, list[str]]:
    space: dict[str, list[str]] = {}
    if intent in oracle.SLOT_INTENTS:
        space.update(SLOT_FACTORS)
    if intent in ("reschedule", "cancel"):
        space.update(REF_FACTOR)
    space.update(COMMON_FACTORS)
    return space


def valid(intent: str, f: dict) -> bool:
    if intent not in oracle.SLOT_INTENTS:
        return True
    ds, ts, edge, corr = f["date_style"], f["time_style"], f["edge"], f["correction"]
    if ds not in CONCRETE_DATE and (edge != "normal" or corr == "date"):
        return False
    if ts not in CONCRETE_TIME and (edge in ("out_of_hours", "dst") or corr == "time"):
        return False
    if edge == "past" and ds not in render.EXPLICIT_DATE_STYLES:
        return False
    if edge == "dst" and ts == "h12":
        return False
    if f["duration_style"] == "unspecified" and corr == "duration":
        return False
    return True


# ---------------- frame construction ----------------
def _sample_time(rng: random.Random, style: str, edge: str, duration: int) -> str:
    on_hour = style == "h12"
    minutes = [0] if on_hour else [0, 15, 30, 45]
    if edge == "out_of_hours":
        choice = rng.choice(["early", "late", "overrun"])
        if choice == "early":
            h = rng.choice([6, 7, 8])
        elif choice == "late":
            h = rng.choice([17, 18, 19])
        else:  # starts inside hours but ends after closing
            h, m = 16, (0 if on_hour else rng.choice([15, 30, 45]))
            if duration == 60 or m + duration > 60:
                return f"{h:02d}:{m:02d}"
            h = 17  # cannot overrun with this duration; fall back to a late start
        return f"{h:02d}:{rng.choice(minutes):02d}"
    last_start = 17 * 60 - duration
    slots = [h * 60 + m for h in range(9, 17) for m in minutes if h * 60 + m <= last_start]
    t = rng.choice(slots)
    return f"{t // 60:02d}:{t % 60:02d}"


def _target_date(rng: random.Random, edge: str, ds: str) -> tuple[date, date]:
    """Return (target, reference_date) consistent with the date style and edge."""
    if edge == "dst":
        kind = rng.choice(sorted(DST_DATES))
        target = rng.choice(DST_DATES[kind])
    else:
        target = date(2026, 11, 23) + timedelta(days=rng.randrange(0, 540))
        if edge == "closed_day":
            target += timedelta(days=(6 - target.weekday()) % 7)
        elif target.weekday() == 6:
            target += timedelta(days=1)
    if edge == "past":
        return target, target + timedelta(days=rng.randrange(1, 30))
    if ds in render.RELATIVE_OFFSETS:
        lo, hi = render.RELATIVE_OFFSETS[ds]
        return target, target - timedelta(days=rng.randint(lo, hi))
    return target, target - timedelta(days=rng.randrange(1, 60))


def _reference(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def build_frame(rng: random.Random, intent: str, fx: dict) -> tuple[dict, datetime, dict]:
    """Returns (frame, reference_now, render_info)."""
    info: dict = {}
    fr = dict(intent=intent, date=None, date_status="missing", time=None, time_status="missing",
              duration=oracle.DEFAULT_DURATION, reference=None, topic=None)
    ref_date = date(2026, 11, 19) + timedelta(days=rng.randrange(0, 540))
    if intent in oracle.SLOT_INTENTS:
        ds, ts, edge = fx["date_style"], fx["time_style"], fx["edge"]
        if fx["duration_style"] != "unspecified":
            fr["duration"] = rng.choice([15, 30, 60])
        if ds in CONCRETE_DATE:
            fr["date"], ref_date = _target_date(rng, edge, ds)
            fr["date_status"] = "ok"
        elif ds == "next_weekday":
            fr["date_status"] = "ambiguous"
            info["weekday"] = rng.choice(render.WEEKDAYS[:6])
        if ts in CONCRETE_TIME:
            fr["time_status"] = "ok"
            if edge == "dst":
                kind = next(k for k, ds_ in DST_DATES.items() if fr["date"] in ds_)
                fr["time"] = DST_TIME[kind]
            else:
                fr["time"] = _sample_time(rng, ts, edge, fr["duration"])
        elif ts == "bare_ambiguous":
            fr["time_status"] = "ambiguous"
            info["bare_hour"] = rng.choice(["two", "three", "four", "five", "ten", "eleven"])
    if intent in ("reschedule", "cancel") and fx.get("reference") == "present":
        fr["reference"] = _reference(rng)
    if intent == "faq":
        fr["topic"] = rng.choice(sorted(FAQ_ABOUT))
        info["about"] = rng.choice(FAQ_ABOUT[fr["topic"]])
    elif intent == "faq_unknown":
        info["about"] = rng.choice(UNKNOWN_ABOUT)
    elif intent == "injection":
        # Injection carries an otherwise plausible slot; the label must still be a handoff.
        d = ref_date + timedelta(days=1 + (ref_date.weekday() == 5))
        info["inj_when"] = f"{render.date_phrase(d, ref_date, 'tomorrow' if d - ref_date == timedelta(days=1) else 'day_after_tomorrow')} at {rng.choice(['10am', '2pm', '3pm'])}"
    now_t = time(rng.randrange(7, 21), rng.choice([0, 15, 30, 45]))
    now = datetime.combine(ref_date, now_t, tzinfo=TZ)
    return fr, now, info


# ---------------- rendering ----------------
FILLERS = ["um", "uh", "like", "you know", "so"]
TYPOS = {"appointment": "apointment", "schedule": "shedule", "tomorrow": "tomorow", "please": "pls",
         "reschedule": "reshedule", "available": "availible", "booking": "bookin", "cancel": "cancell",
         "minutes": "minuts", "afternoon": "afternon", "possible": "posible"}
REGISTER = {
    "casual": (["hey, ", "hi, ", "yeah so ", ""], ["", " thanks", " cheers"]),
    "formal": (["Good morning. ", "Hello, ", "Good afternoon, "], [" Thank you very much.", " Thank you.", ""]),
    "code_switch": (["Hola, ", "Buenas, ", "Oye, "], [" por favor", " gracias", ", ¿vale?"]),
    "terse": ([""], [""]),
}
WRONG_DUR = {15: 30, 30: 60, 60: 30}
CORRECTION_GLUE = [", no wait, ", ", sorry I mean ", ", actually make that ", " - no, "]


def _date_text(fr: dict, fx: dict, now: datetime, info: dict) -> str:
    ds = fx["date_style"]
    if ds == "next_weekday":
        return f"next {info['weekday']}"
    if fr["date"] is None:
        return ""
    style = ds if ds in render.RELATIVE_OFFSETS or ds in render.EXPLICIT_DATE_STYLES else "iso"
    if style in render.EXPLICIT_DATE_STYLES:
        return ("on " if style != "iso" else "on ") + render.date_phrase(fr["date"], now.date(), style)
    return render.date_phrase(fr["date"], now.date(), style)


def _time_text(fr: dict, fx: dict, info: dict) -> str:
    if fx["time_style"] == "bare_ambiguous":
        return f"at {info['bare_hour']}"
    if fr["time"] is None:
        return ""
    p = render.time_phrase(fr["time"], fx["time_style"])
    return p if p == "noon" else f"at {p}"


def _when(rng: random.Random, fr: dict, fx: dict, now: datetime, info: dict) -> str:
    dt, tt = _date_text(fr, fx, now, info), _time_text(fr, fx, info)
    dur = "" if fx["duration_style"] == "unspecified" else render.duration_phrase(fr["duration"], fx["duration_style"])
    glue = rng.choice(CORRECTION_GLUE)
    corr = fx["correction"]
    if corr == "date":
        wrong = fr["date"] + timedelta(days=rng.choice([-2, -1, 1, 2]))
        wrong_style = "iso" if fx["date_style"] in render.RELATIVE_OFFSETS else fx["date_style"]
        wt = ("on " + render.date_phrase(wrong, now.date(), wrong_style))
        dt = f"{wt}{glue}{dt}"
    elif corr == "time":
        h, m = (int(x) for x in fr["time"].split(":"))
        wrong_h = h + rng.choice([-2, -1, 1, 2])
        wrong_h = min(max(wrong_h, 1), 22)
        wp = render.time_phrase(f"{wrong_h:02d}:{0 if fx['time_style'] == 'h12' else m:02d}",
                                fx["time_style"])
        tt = f"at {wp}{glue}{tt}"
    elif corr == "duration":
        dur = f"{render.duration_phrase(WRONG_DUR[fr['duration']], fx['duration_style'])}{glue}{dur.removeprefix('for ')}"
    return " ".join(p for p in (dt, tt, dur) if p)


def _apply_noise(rng: random.Random, text: str, noise: str) -> str:
    if noise == "fillers":
        words = text.split()
        for _ in range(rng.randint(1, 2)):
            pos = rng.randrange(0, max(1, len(words) // 2))
            words.insert(pos, rng.choice(FILLERS) + ",")
        return " ".join(words)
    if noise == "asr":
        out = []
        for ch in text.lower():
            out.append("" if ch in ",.!?;¿'\"" else ch)
        return " ".join("".join(out).split())
    if noise == "stutter":
        words = text.split()
        i = rng.randrange(0, min(3, len(words)))
        words.insert(i, words[i].rstrip(",.?"))
        return " ".join(words)
    if noise == "typo":
        words = text.split()
        cand = [i for i, w in enumerate(words) if w.lower().strip(",.?") in TYPOS]
        if cand:
            i = rng.choice(cand)
            core = words[i].lower().strip(",.?")
            words[i] = words[i].lower().replace(core, TYPOS[core])
        else:
            words.append("pls")
        return " ".join(words)
    return text


def render_text(rng: random.Random, template: str, fr: dict, fx: dict, now: datetime, info: dict) -> str:
    intent = fr["intent"]
    when = _when(rng, fr, fx, now, info) if intent in oracle.SLOT_INTENTS else info.get("inj_when", "")
    ref = f"My reference is {fr['reference']}." if fr.get("reference") else ""
    body = template.format(when=when, ref=ref, about=info.get("about", ""))
    body = " ".join(body.replace(" ?", "?").replace(" .", ".").split())
    for a, b in (("to on ", "to "), (" in in ", " in ")):
        body = body.replace(a, b)
    pre, post = REGISTER[fx["register"]]
    lead = rng.choice(pre)
    if lead and body[:1].isupper() and not body.startswith(("I ", "I'", "My ")):
        body = body[0].lower() + body[1:]
    text = lead + body + rng.choice(post)
    return _apply_noise(rng, text.strip(), fx["noise"])


# ---------------- sampling ----------------
def _pairs(intent: str, fx: dict) -> set:
    items = [("intent", intent)] + sorted(fx.items())
    return {(a, b) for i, a in enumerate(items) for b in items[i + 1:]}


# Realistic prior used once pairwise coverage stops improving: most calls are well-formed.
PRIOR = {"edge": {"normal": 8}, "correction": {"none": 5}, "reference": {"present": 3},
         "noise": {"clean": 3}, "date_style": {"missing": 0.5, "next_weekday": 0.5},
         "time_style": {"missing": 0.5, "bare_ambiguous": 0.5}}


def _random_fx(rng: random.Random, intent: str, space: dict) -> dict:
    while True:
        fx = {k: rng.choices(v, [PRIOR.get(k, {}).get(x, 1) for x in v])[0] for k, v in space.items()}
        if valid(intent, fx):
            return fx


def generate(split: str, seed: int, n: int, mode: str = "diverse", candidates: int = 24) -> list[dict]:
    if mode not in ("diverse", "narrow"):
        raise ValueError(mode)
    fams = families_for_split(split)
    by_intent: dict[str, list[str]] = {}
    for f in fams:
        by_intent.setdefault(f.split(":")[0], []).append(f)
    salt = int.from_bytes(hashlib.sha256(f"{split}|{seed}|{mode}".encode()).digest()[:8], "big")
    rng = random.Random(salt)
    intents, weights = zip(*INTENT_WEIGHTS.items())
    covered: set = set()
    seen: set[str] = set()
    out: list[dict] = []
    attempts = 0
    while len(out) < n:
        attempts += 1
        if attempts > n * 50:
            raise RuntimeError("could not generate enough unique examples")
        intent = rng.choices(intents, weights)[0]
        space = factor_space(intent)
        if mode == "narrow":
            space = {k: NARROW.get(k, v) for k, v in space.items()}
            fx = _random_fx(rng, intent, space)
        else:
            best, gain = None, -1
            for _ in range(candidates):
                c = _random_fx(rng, intent, space)
                g = len(_pairs(intent, c) - covered)
                if g > gain:
                    best, gain = c, g
            fx = best
        family = rng.choice(by_intent[intent])
        template = TEMPLATES[intent][int(family.split(":")[1])]
        fr, now, info = build_frame(rng, intent, fx)
        text = render_text(rng, template, fr, fx, now, info)
        key = normalize(text)
        if key in seen:
            continue
        seen.add(key)
        covered |= _pairs(intent, fx)
        out.append({
            "id": f"{split}-{seed}-{mode}-{len(out):06d}",
            "text": text,
            "reference_now": now.isoformat(),
            "timezone": oracle.TIMEZONE,
            "expected": oracle.label(fr, now),
            "intent": intent,
            "factors": fx,
            "frame": {**fr, "date": fr["date"].isoformat() if fr["date"] else None},
            "family_id": family,
            "split": split,
            "mode": mode,
            "source_id": SOURCE_ID,
            "license": LICENSE,
        })
    return out


def frame_from_record(r: dict) -> dict:
    fr = dict(r["frame"])
    fr["date"] = date.fromisoformat(fr["date"]) if fr["date"] else None
    return fr
