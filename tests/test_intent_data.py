"""Intent data for the tiny brain (lab/intents): factorised, diverse, filtered.
Business-specialised classes + conversational moves; never touches the sealed holdout."""
from collections import Counter

from lab.intents.generate import CLASSES, generate, normalise, dedupe, balance


def test_every_class_is_generated_with_variety():
    rows = generate(seed=0, per_class=120)
    counts = Counter(r["intent"] for r in rows)
    assert set(counts) == set(CLASSES)
    for c in CLASSES:
        texts = {normalise(r["text"]) for r in rows if r["intent"] == c}
        assert len(texts) >= 80, (c, len(texts))           # mostly distinct phrasings


def test_dedupe_removes_normalised_repeats():
    rows = [{"text": "Hi, how are you?", "intent": "small_talk"}, {"text": "hi how are you", "intent": "small_talk"},
            {"text": "Book me Friday", "intent": "book"}]
    assert len(dedupe(rows)) == 2


def test_dedupe_drops_texts_that_appear_under_two_labels():
    rows = [{"text": "okay", "intent": "affirm"}, {"text": "Okay.", "intent": "goodbye"},
            {"text": "yes please", "intent": "affirm"}]
    assert [r["text"] for r in dedupe(rows)] == ["yes please"]


def test_balance_caps_each_class():
    rows = [{"text": f"t{i}", "intent": "a"} for i in range(50)] + [{"text": f"u{i}", "intent": "b"} for i in range(5)]
    assert Counter(r["intent"] for r in balance(rows, cap=10, seed=0)) == {"a": 10, "b": 5}


def test_generation_is_deterministic_and_seeded():
    assert generate(seed=1, per_class=20) == generate(seed=1, per_class=20)
    assert generate(seed=1, per_class=20) != generate(seed=2, per_class=20)


def test_business_phrases_cover_the_websites_topics():
    rows = [r for r in generate(seed=0, per_class=200) if r["intent"] == "business"]
    joined = " ".join(r["text"].lower() for r in rows)
    for topic in ("website", "receptionist", "price", "passaic", "get started", "automation"):
        assert topic in joined, topic
