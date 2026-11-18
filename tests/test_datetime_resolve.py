"""S3: deterministic spoken date/time resolution. The code decides; a model (or
ASR text) only supplies the phrase. Anything ambiguous returns None so the
dialogue asks instead of guessing. `now` is injected for determinism."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from receptionist.datetime_resolve import resolve

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 18, 9, 0, tzinfo=NY)          # a Wednesday


@pytest.mark.parametrize("phrase,expect", [
    ("december tenth at two pm", (2026, 12, 10, 14, 0)),
    ("Book December 10th at 2 p.m.", (2026, 12, 10, 14, 0)),
    ("december 10 at 2pm", (2026, 12, 10, 14, 0)),
    ("january third at nine thirty am", (2027, 1, 3, 9, 30)),        # next occurrence -> next year
    ("march fifteenth at one pm", (2027, 3, 15, 13, 0)),
    ("february twenty eighth at four pm", (2027, 2, 28, 16, 0)),
    ("april second at noon", (2027, 4, 2, 12, 0)),
    ("tomorrow at 10 am", (2026, 11, 19, 10, 0)),
    ("today at 3:15 pm", (2026, 11, 18, 15, 15)),
    ("friday at nine thirty am", (2026, 11, 20, 9, 30)),
    ("monday at eleven am", (2026, 11, 23, 11, 0)),
    ("next tuesday at 14:00", (2026, 11, 24, 14, 0)),
    ("the 10th of december at 2 pm", (2026, 12, 10, 14, 0)),
])
def test_resolves_spoken_dates(phrase, expect):
    got = resolve(phrase, NOW)
    assert got is not None, phrase
    assert (got.year, got.month, got.day, got.hour, got.minute) == expect
    assert got.tzinfo is not None and got.utcoffset() == got.astimezone(NY).utcoffset()


@pytest.mark.parametrize("phrase", [
    "december tenth",                 # no time
    "at two",                         # no date, no am/pm
    "friday at two",                  # hour without am/pm is ambiguous
    "december at 2 pm",               # no day
    "february thirtieth at 2 pm",     # impossible date
    "today at 8 am",                  # already past
    "book something nice",
    "",
    "march 14 2027 at 2:30 am",       # DST gap in New York (2nd Sunday of March 2027): nonexistent
])
def test_refuses_ambiguous_or_impossible(phrase):
    assert resolve(phrase, NOW) is None


def test_explicit_24h_time_needs_no_ampm():
    assert resolve("december 10 at 14:30", NOW).hour == 14


def test_result_is_after_now():
    got = resolve("november 18 at 10 am", NOW)
    assert got is not None and got > NOW
