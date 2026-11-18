"""Short run of scripts/soak_calls.py: real uvicorn in a child process, several
simultaneous callers. Invariant: every successful call produced exactly one
booking, zero failures, zero overlaps, DB integrity ok."""
import sys
from pathlib import Path

import pytest

# Importable by name so spawned server processes can unpickle its functions.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import soak_calls  # noqa: E402

pytestmark = pytest.mark.timeout(120)


def test_short_soak_two_simultaneous_callers():
    s = soak_calls.soak(minutes=0.1, callers=2)
    assert s["calls_ok"] >= 2, s
    assert s["calls_failed"] == 0, s["errors"]
    assert s["bookings"] == s["calls_ok"]
    assert s["overlaps"] == 0 and s["integrity"] == "ok"


def test_slots_are_unique_weekdays_in_hours():
    import itertools
    got = list(itertools.islice(soak_calls.slots(), 40))
    assert len(set(got)) == 40
    assert all("09:00" <= g[-5:] <= "16:30" for g in got)
