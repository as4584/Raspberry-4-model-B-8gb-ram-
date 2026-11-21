"""Business profile: one file the owner fills in. Name -> greeting; hours -> booking
rules and answers; faq answers only from the file (never invented)."""
import json
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from receptionist.booking import BookingStore
from receptionist.brain import Brain
from receptionist.profile import greeting_for, load_profile

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=NY)
ROOT = Path(__file__).resolve().parents[1]


class Intents:
    def classify(self, t):
        t = t.lower()
        if any(w in t for w in ("price", "cost", "charge", "do you do", "services", "offer")):
            return "faq", 0.9
        return "faq_unknown", 0.4


def profile(tmp_path, **over):
    p = {"name": "LexMakesIt", "hours": {"mon": "10:00-18:00", "wed": "10:00-18:00", "sat": "09:00-13:00"},
         "faq": {"services": "We build custom websites and AI tools for small businesses.",
                 "price": "Websites start at five hundred dollars."}}
    p.update(over)
    f = tmp_path / "profile.json"
    f.write_text(json.dumps(p))
    return load_profile(str(f))


def test_greeting_uses_the_business_name_and_stays_cacheable(tmp_path):
    g = greeting_for(profile(tmp_path))
    assert g == "Hi, thanks for calling LexMakesIt. How can I help you today?"
    assert all(len(s) <= 40 for s in g.split(". "))


def test_hours_come_from_the_profile(tmp_path):
    prof = profile(tmp_path)
    assert prof["hours_parsed"] == {0: (time(10), time(18)), 2: (time(10), time(18)), 5: (time(9), time(13))}
    b = Brain(BookingStore(str(tmp_path / "b.sqlite")), intents=Intents(), clock=lambda: NOW, profile=prof)
    assert b.hours == prof["hours_parsed"]


def test_answers_only_from_the_profile(tmp_path):
    b = Brain(BookingStore(str(tmp_path / "b.sqlite")), intents=Intents(), clock=lambda: NOW,
              profile=profile(tmp_path))
    assert "custom websites" in b.handle("What services do you offer?", "c").speech
    assert "five hundred" in b.handle("How much does a website cost?", "c").speech
    r = b.handle("Do you take insurance?", "c")
    assert r.action == "unknown"                       # not in the profile -> never invented


def test_shipped_profile_has_the_business_name_and_no_made_up_facts():
    p = json.loads((ROOT / "deploy" / "business_profile.json").read_text())
    assert p["name"] == "LexMakesIt"
    assert all(v in ("", None) for v in p.get("faq", {}).values())   # owner fills these in
