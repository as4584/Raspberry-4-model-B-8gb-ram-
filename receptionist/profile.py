"""Business profile (deploy/business_profile.json): the owner's facts, nothing else.
The brain greets with the name, books within the hours, and answers questions only
from `faq`; an empty or missing answer means "I'll have someone call you back"."""
from __future__ import annotations

import json
from datetime import time

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# Which faq key answers which kind of question (matched against the caller's words).
TOPICS = {
    "get_started": ("get started", "getting started", "how do i start", "sign up", "first step", "consultation",
                    "demo", "free trial"),
    "price": ("price", "cost", "charge", "how much", "rate", "fee", "pricing", "expensive"),
    "services": ("service", "do you do", "what do you", "offer", "what kind", "specialize", "help with"),
    "location": ("where", "address", "located", "location", "directions", "area", "serve"),
    "parking": ("parking", "park"),
    "payment": ("pay", "payment", "card", "cash", "venmo", "zelle"),
    "contact": ("email", "website", "contact"),
}


PRICED_PER_PROJECT = ("receptionist", "software", "app", "point of sale", "pos", "automation", "agent",
                      "inventory", "text back", "review", "reminder", "content", "google", "listing")


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def load_profile(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    parsed = {}
    for day, span in (p.get("hours") or {}).items():
        if span:
            a, b = span.split("-")
            parsed[DAYS.index(day.lower()[:3])] = (_hm(a), _hm(b))
    p["hours_parsed"] = parsed or None
    return p


def greeting_for(profile: dict | None) -> str:
    name = (profile or {}).get("name")
    return f"Hi, thanks for calling {name}. How can I help you today?" if name else \
        "Hi, thanks for calling. How can I help you today?"


def answer(profile: dict | None, text: str) -> tuple[str, str | None]:
    """(topic, answer) for the caller's question; answer None when the profile has none."""
    t = text.lower()
    faq = (profile or {}).get("faq") or {}
    # The site prices only websites and teaching; any other product's price is a quote,
    # never the website figure.
    if any(w in t for w in TOPICS["price"]) and any(w in t for w in PRICED_PER_PROJECT) \
            and not any(w in t for w in ("website", "web site", "teaching", "lesson", "class")):
        return "price", "That's priced for each business, so I can have someone call you back with a quote."
    for topic, words in TOPICS.items():
        if any(w in t for w in words):
            return topic, (faq.get(topic) or None)
    return "", None
