"""S8: /readyz reports whether the Pi can actually take calls right now:
DB write lock, free disk, power (vcgencmd throttled bits) and temperature.
503 + per-check detail when any fails. Probes are injectable for tests."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from receptionist import health
from receptionist.server import Settings, create_app


def client(tmp_path, monkeypatch, throttled="0x0", temp_c=45.0, free_mb=5000):
    monkeypatch.setattr(health, "read_throttled", lambda: throttled)
    monkeypatch.setattr(health, "read_temp_c", lambda: temp_c)
    monkeypatch.setattr(health, "free_mb", lambda path: free_mb)
    return TestClient(create_app(Settings(db_path=str(tmp_path / "h.sqlite"), dev_allow_unsigned=False)))


def test_ready_when_everything_ok(tmp_path, monkeypatch):
    r = client(tmp_path, monkeypatch).get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert {c["name"] for c in body["checks"]} == {"database", "disk", "power", "temperature"}


@pytest.mark.parametrize("kw,name", [({"throttled": "0x50005"}, "power"), ({"throttled": "0x1"}, "power"),
                                     ({"temp_c": 82.0}, "temperature"), ({"free_mb": 100}, "disk")])
def test_not_ready_names_the_failing_check(tmp_path, monkeypatch, kw, name):
    r = client(tmp_path, monkeypatch, **kw).get("/readyz")
    assert r.status_code == 503 and r.json()["ready"] is False
    failed = [c["name"] for c in r.json()["checks"] if not c["ok"]]
    assert failed == [name]


def test_past_undervoltage_is_reported_but_ready(tmp_path, monkeypatch):
    r = client(tmp_path, monkeypatch, throttled="0x50000").get("/readyz")
    assert r.status_code == 200
    power = next(c for c in r.json()["checks"] if c["name"] == "power")
    assert power["ok"] is True and "since boot" in power["detail"]


def test_unknown_power_and_temp_off_pi_are_ok(tmp_path, monkeypatch):
    r = client(tmp_path, monkeypatch, throttled=None, temp_c=None).get("/readyz")
    assert r.status_code == 200


def test_locked_database_is_not_ready(tmp_path, monkeypatch):
    c = client(tmp_path, monkeypatch)
    c.get("/healthz")                          # app created; DB exists
    holder = sqlite3.connect(tmp_path / "h.sqlite", isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        r = c.get("/readyz")
        assert r.status_code == 503
        assert [x["name"] for x in r.json()["checks"] if not x["ok"]] == ["database"]
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_parse_helpers():
    assert health.parse_throttled("throttled=0x50005") == "0x50005"
    assert health.parse_throttled("garbage") is None
    assert health.parse_temp("temp=48.3'C") == 48.3
    assert health.parse_temp("nope") is None
