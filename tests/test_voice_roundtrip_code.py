"""The voice harness fills a {code} placeholder from the DB before speaking a
turn, so book -> yes -> "cancel {code}" can be exercised end to end in one call."""
import importlib.util
import sqlite3
from pathlib import Path

spec = importlib.util.spec_from_file_location("vr", Path(__file__).parents[1] / "scripts" / "voice_roundtrip.py")
vr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vr)


def _db(tmp_path, rows):
    p = tmp_path / "v.sqlite"
    db = sqlite3.connect(p)
    db.execute("CREATE TABLE bookings (status TEXT, code TEXT, starts TEXT)")
    db.executemany("INSERT INTO bookings VALUES(?,?,?)", rows)
    db.commit(); db.close()
    return str(p)


def test_line_without_placeholder_unchanged(tmp_path):
    assert vr.fill_code("book friday at noon", _db(tmp_path, [])) == "book friday at noon"


def test_placeholder_becomes_spoken_digits_of_latest_active_code(tmp_path):
    db = _db(tmp_path, [("cancelled", "1111", "a"), ("active", "4072", "b")])
    assert vr.fill_code("cancel booking {code}", db) == "cancel booking four zero seven two"


def test_placeholder_without_active_booking_fails_loudly(tmp_path):
    import pytest
    with pytest.raises(SystemExit):
        vr.fill_code("cancel {code}", _db(tmp_path, []))
