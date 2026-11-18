"""/simulate/text keeps one Dialogue per session_id; the map must be bounded (LRU)."""
from fastapi.testclient import TestClient

import receptionist.server as server
from receptionist.server import Settings, create_app


def app_with(tmp_path, **kw):
    return create_app(Settings(db_path=str(tmp_path / "s.sqlite"), dev_allow_unsigned=True), **kw)


def say(c, session, text):
    return c.post("/simulate/text", json={"session_id": session, "text": text}).json()


def test_default_cap_is_1000(tmp_path):
    assert server.MAX_SIM_SESSIONS == 1000
    app = app_with(tmp_path)
    c = TestClient(app)
    for i in range(1001):
        say(c, f"s{i}", "hello")
    assert len(app.state.sessions) == 1000
    assert "s0" not in app.state.sessions and "s1000" in app.state.sessions


def test_least_recently_used_session_is_evicted(tmp_path):
    app = app_with(tmp_path, max_sessions=2)
    c = TestClient(app)
    assert say(c, "a", "book 2027-12-10 10:00")["action"] == "confirm_required"
    say(c, "b", "hello")
    say(c, "a", "hello")            # touch a: b is now least recently used
    say(c, "c", "hello")
    assert list(app.state.sessions) == ["a", "c"]
    assert say(c, "a", "yes")["action"] == "booked"   # a's pending survived


def test_evicted_session_loses_pending_and_cannot_confirm(tmp_path):
    app = app_with(tmp_path, max_sessions=1)
    c = TestClient(app)
    say(c, "a", "book 2027-12-10 10:00")
    say(c, "b", "hello")
    assert len(app.state.sessions) == 1
    r = say(c, "a", "yes")
    assert (r["action"], r["text"]) == ("reply", "There is no pending booking to confirm.")
    assert app.state.store.active() == []
