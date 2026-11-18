"""#38: replies spoken on the phone use words, not ISO dates or UUIDs."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from receptionist.speech_format import spoken_when

NY = ZoneInfo("America/New_York")


@pytest.mark.parametrize("dt,expect", [
    (datetime(2026, 12, 10, 14, 0, tzinfo=NY), "Thursday, December tenth at two p m"),
    (datetime(2027, 1, 3, 9, 30, tzinfo=NY), "Sunday, January third at nine thirty a m"),
    (datetime(2027, 2, 28, 16, 5, tzinfo=NY), "Sunday, February twenty eighth at four oh five p m"),
    (datetime(2026, 12, 1, 12, 0, tzinfo=NY), "Tuesday, December first at noon"),
    (datetime(2026, 12, 21, 0, 15, tzinfo=NY), "Monday, December twenty first at twelve fifteen a m"),
    (datetime(2026, 12, 22, 23, 45, tzinfo=NY), "Tuesday, December twenty second at eleven forty five p m"),
])
def test_spoken_when(dt, expect):
    assert spoken_when(dt) == expect


def test_spoken_when_converts_to_business_timezone():
    utc = datetime(2026, 12, 10, 19, 0, tzinfo=ZoneInfo("UTC"))
    assert spoken_when(utc, NY) == "Thursday, December tenth at two p m"


@pytest.mark.parametrize("n,expect", [(15, "fifteen"), (30, "thirty"), (60, "sixty"), (90, "ninety"), (45, "forty five")])
def test_durations_are_spoken(n, expect):
    from receptionist.speech_format import _cardinal
    assert _cardinal(n) == expect
