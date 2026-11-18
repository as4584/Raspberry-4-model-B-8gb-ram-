"""S2 booking-safety gates. Mandatory invariants: no double-booking, no false
confirmation, no cross-call state, no unsafe write on failure.
LAPTOP/MOCK evidence here; re-run on the Pi for PI4 evidence."""
import multiprocessing as mp
import random
from datetime import datetime, timedelta, timezone

import pytest

from receptionist.booking import BookingStore, InvalidBooking, KeyConflict, SlotTaken
from receptionist.dialogue import Dialogue

T0 = datetime(2027, 1, 20, 14, tzinfo=timezone.utc)


# 1. Cross-session isolation -------------------------------------------------

def test_one_callers_yes_never_confirms_another_callers_pending(tmp_path):
    store = BookingStore(str(tmp_path / "s.sqlite"))
    d = Dialogue(store)
    assert d.handle("book 2027-12-10 14:00", "caller-A").action == "confirm_required"
    r = d.handle("yes", "caller-B")
    assert r.action != "booked" and r.committed_id is None
    assert store.active() == []
    assert d.handle("yes", "caller-A").action == "booked"
    assert len(store.active()) == 1


def test_one_callers_no_does_not_clear_another_callers_pending(tmp_path):
    store = BookingStore(str(tmp_path / "s.sqlite"))
    d = Dialogue(store)
    d.handle("book 2027-12-10 14:00", "caller-A")
    d.handle("no", "caller-B")
    assert d.handle("yes", "caller-A").action == "booked"


# 2. Separate OS processes racing one slot ------------------------------------

def _race_worker(path, i, start_iso, q):
    start = datetime.fromisoformat(start_iso)
    try:
        q.put(BookingStore(path).book("r", start, start + timedelta(minutes=30), f"p{i}")["id"])
    except SlotTaken:
        q.put(None)
    except Exception as exc:  # surfaced to the parent as a failure
        q.put(f"ERROR {exc!r}")


def test_slot_race_across_processes_exactly_one_commit(tmp_path):
    path = str(tmp_path / "proc.sqlite")
    BookingStore(path)
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_race_worker, args=(path, i, T0.isoformat(), q)) for i in range(8)]
    for p in procs:
        p.start()
    results = [q.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(timeout=60)
    assert not [r for r in results if isinstance(r, str) and r.startswith("ERROR")], results
    assert sum(r is not None for r in results) == 1
    assert len(BookingStore(path).active()) == 1


# 3. Seeded 1000-operation battery vs. a reference model ---------------------

def _overlaps(a, b):
    return a[0] < b[1] and a[1] > b[0]


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_seeded_battery_matches_oracle_and_never_overlaps(tmp_path, seed):
    rng = random.Random(seed)
    store = BookingStore(str(tmp_path / f"b{seed}.sqlite"))
    oracle_active: dict[str, tuple] = {}          # id -> (resource, start, end)
    oracle_keys: dict[str, tuple] = {}            # idem_key -> (payload, id)
    for n in range(1000):
        res = rng.choice(["r1", "r2", "r3"])
        start = T0 + timedelta(minutes=15 * rng.randrange(0, 40))
        end = start + timedelta(minutes=rng.choice([15, 30, 60]))
        op = rng.random()
        if op < 0.15 and oracle_keys:             # replay an old key, same payload
            key = rng.choice(list(oracle_keys))
            payload, _ = oracle_keys[key]
            res, start, end = payload
        elif op < 0.25 and oracle_keys:           # reuse a key with a different payload
            key = rng.choice(list(oracle_keys))
        elif op < 0.35 and oracle_active:         # cancel
            ref = rng.choice(list(oracle_active))
            assert store.cancel(ref) is True
            del oracle_active[ref]
            assert store.cancel(ref) is False
            continue
        else:
            key = f"k{seed}-{n}"
        payload = (res, start, end)
        if key in oracle_keys:
            prev_payload, prev_id = oracle_keys[key]
            if prev_payload == payload:
                assert store.book(res, start, end, key)["id"] == prev_id
            else:
                with pytest.raises(KeyConflict):
                    store.book(res, start, end, key)
            continue
        clash = any(r == res and _overlaps((s, e), (start, end)) for r, s, e in oracle_active.values())
        if clash:
            with pytest.raises(SlotTaken):
                store.book(res, start, end, key)
        else:
            row = store.book(res, start, end, key)
            oracle_active[row["id"]] = payload
            oracle_keys[key] = (payload, row["id"])
    rows = store.active()
    assert {r["id"] for r in rows} == set(oracle_active)
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            assert not (a["resource"] == b["resource"] and a["starts"] < b["ends"] and a["ends"] > b["starts"])


# 4. Atomic reschedule --------------------------------------------------------

def test_reschedule_moves_booking_atomically(tmp_path):
    store = BookingStore(str(tmp_path / "rs.sqlite"))
    old = store.book("r", T0, T0 + timedelta(minutes=30), "k1")
    new = store.reschedule(old["id"], T0 + timedelta(hours=2), T0 + timedelta(hours=2, minutes=30), "rs1")
    act = store.active()
    assert [r["id"] for r in act] == [new["id"]] and new["id"] != old["id"]


def test_reschedule_into_taken_slot_keeps_original(tmp_path):
    store = BookingStore(str(tmp_path / "rs.sqlite"))
    old = store.book("r", T0, T0 + timedelta(minutes=30), "k1")
    store.book("r", T0 + timedelta(hours=2), T0 + timedelta(hours=2, minutes=30), "k2")
    with pytest.raises(SlotTaken):
        store.reschedule(old["id"], T0 + timedelta(hours=2), T0 + timedelta(hours=2, minutes=30), "rs1")
    assert old["id"] in {r["id"] for r in store.active()}
    assert len(store.active()) == 2


def test_reschedule_may_overlap_its_own_old_slot(tmp_path):
    store = BookingStore(str(tmp_path / "rs.sqlite"))
    old = store.book("r", T0, T0 + timedelta(minutes=60), "k1")
    new = store.reschedule(old["id"], T0 + timedelta(minutes=30), T0 + timedelta(minutes=90), "rs1")
    assert [r["id"] for r in store.active()] == [new["id"]]


def test_reschedule_unknown_or_cancelled_reference_is_rejected(tmp_path):
    store = BookingStore(str(tmp_path / "rs.sqlite"))
    old = store.book("r", T0, T0 + timedelta(minutes=30), "k1")
    store.cancel(old["id"])
    for ref in (old["id"], "not-a-reference"):
        with pytest.raises(InvalidBooking):
            store.reschedule(ref, T0 + timedelta(hours=1), T0 + timedelta(hours=1, minutes=30), "rs-" + ref)
    assert store.active() == []


def test_reschedule_replay_is_idempotent(tmp_path):
    store = BookingStore(str(tmp_path / "rs.sqlite"))
    old = store.book("r", T0, T0 + timedelta(minutes=30), "k1")
    a = store.reschedule(old["id"], T0 + timedelta(hours=1), T0 + timedelta(hours=1, minutes=30), "rs1")
    b = store.reschedule(old["id"], T0 + timedelta(hours=1), T0 + timedelta(hours=1, minutes=30), "rs1")
    assert a["id"] == b["id"] and len(store.active()) == 1
