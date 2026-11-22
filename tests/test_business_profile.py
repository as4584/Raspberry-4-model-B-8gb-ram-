"""Business profile: one file the owner fills in. Name -> greeting; hours -> booking
rules and answers; faq answers only from the file (never invented)."""
import json
import re
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


def test_shipped_profile_uses_only_facts_from_lexmakesit_com():
    p = json.loads((ROOT / "deploy" / "business_profile.json").read_text())
    assert p["name"] == "LexMakesIt" and "lexmakesit.com" in p["_source"]
    faq = p["faq"]
    assert "one thousand dollars" in faq["price"] and "ten dollars a month" in faq["price"]
    assert "one hundred dollars" in faq["price"]                       # teaching sessions
    assert "Paterson" in faq["location"] and "Passaic County" in faq["location"]
    assert "five five one" in faq["contact"]
    assert "free" in faq["get_started"]
    for k, v in faq.items():                                          # no figures beyond the site's
        assert not re.search(r"\d", v), (k, v)


def test_get_started_and_receptionist_questions_are_answered(tmp_path):
    prof = load_profile(str(ROOT / "deploy" / "business_profile.json"))
    b = Brain(BookingStore(str(tmp_path / "x.sqlite")), intents=Intents(), clock=lambda: NOW, profile=prof)
    assert "free" in b.handle("How do I get started?", "c").speech
    assert "receptionist" in b.handle("What services do you offer?", "c").speech
    r = b.handle("How much is the AI receptionist?", "c")
    assert "one thousand" not in r.speech and r.action in ("faq", "unknown")    # not a website price
