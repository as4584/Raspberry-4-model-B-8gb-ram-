"""V1 DB-state oracle tests — Ramona (Laptop 2).

Each test drives a full WS call through the server and then verifies
the SQLite booking DB state via an independent BookingStore query.
This is the "separate DB-state oracle" required by V1 spec.

No mocks are used to verify DB state — we query the same SQLite file
that the server wrote to, via a fresh connection.

Environment: LAPTOP / MOCK
"""
import pytest
from fastapi.testclient import TestClient
from receptionist.server import Settings, create_app
from receptionist.audio import MockASR, MockTTS
from receptionist.booking import BookingStore
from tests.sd_harness.helpers import build_app, mulaw_frame, encode


def inbound(payload, sid):
    return {"event": "media", "streamSid": sid,
            "media": {"payload": encode(payload)}}


def speak_then_silence(ws, sid, speech_frames=5, silence_frames=16):
    for _ in range(speech_frames):
        ws.send_json(inbound(mulaw_frame(0x00), sid))
    for _ in range(silence_frames):
        ws.send_json(inbound(mulaw_frame(0xFF), sid))


def drain_to_mark(ws):
    for _ in range(20):
        m = ws.receive_json()
        if m.get("event") == "mark":
            return m
    return None


def run_call(app, sid, speech_sequences=1):
    """Drive `speech_sequences` utterances through a WS call; return action list."""
    actions = []
    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": sid,
                          "start": {"callSid": f"C{sid}"}})
            for _ in range(speech_sequences):
                speak_then_silence(ws, sid)
                drain_to_mark(ws)
            ws.send_json({"event": "stop", "streamSid": sid})
    return actions


def app_with_oracle(tmp_path, transcripts):
    db_path = str(tmp_path / "oracle.sqlite")
    app = create_app(
        Settings(db_path=db_path, dev_allow_unsigned=True,
                 wss_url="wss://localhost/twilio/media"),
        asr_factory=lambda: MockASR(list(transcripts)),
        tts=MockTTS(),
    )
    db = BookingStore(db_path)
    return app, db


# ---------------------------------------------------------------------------
# Single-call booking oracle
# ---------------------------------------------------------------------------

def test_book_yes_creates_one_active_booking(tmp_path):
    app, db = app_with_oracle(tmp_path, ["book 2027-12-10 14:00", "yes"])
    run_call(app, "M1", speech_sequences=2)
    assert len(db.active()) == 1


def test_book_no_creates_zero_bookings(tmp_path):
    app, db = app_with_oracle(tmp_path, ["book 2027-12-10 14:00", "no"])
    run_call(app, "M1", speech_sequences=2)
    assert db.active() == []


def test_book_deny_reply_then_different_slot_books(tmp_path):
    app, db = app_with_oracle(
        tmp_path, ["book 2027-12-10 14:00", "no", "book 2027-12-11 09:00", "yes"])
    run_call(app, "M1", speech_sequences=4)
    active = db.active()
    assert len(active) == 1
    assert "2027-12-11" in active[0]["starts"]


def test_book_confirm_cancel_leaves_zero_active(tmp_path):
    """Full lifecycle: book → yes → cancel reference → DB empty."""
    db_path = str(tmp_path / "lifecycle.sqlite")
    app = create_app(
        Settings(db_path=db_path, dev_allow_unsigned=True,
                 wss_url="wss://localhost/twilio/media"),
        asr_factory=lambda: MockASR(["book 2027-12-10 14:00", "yes"]),
        tts=MockTTS(),
    )
    db = BookingStore(db_path)

    with TestClient(app) as c:
        with c.websocket_connect("/twilio/media") as ws:
            ws.send_json({"event": "start", "streamSid": "M1",
                          "start": {"callSid": "C1"}})
            speak_then_silence(ws, "M1")
            drain_to_mark(ws)
            speak_then_silence(ws, "M1")
            drain_to_mark(ws)
            ws.send_json({"event": "stop", "streamSid": "M1"})

    active = db.active()
    assert len(active) == 1
    ref = active[0]["id"]

    # Cancel via simulate endpoint
    with TestClient(app) as c:
        r = c.post("/simulate/text",
                   json={"session_id": "cancel-session",
                         "text": f"cancel {ref}"})
        assert r.json()["action"] == "cancelled"

    assert db.active() == []


# ---------------------------------------------------------------------------
# Idempotency oracle: re-confirming same utterance doesn't double-book
# ---------------------------------------------------------------------------

def test_simulate_idempotency(tmp_path):
    """Calling simulate/text with 'yes' twice in same session must not double-book."""
    app, db = app_with_oracle(tmp_path, [])
    with TestClient(app) as c:
        c.post("/simulate/text", json={"session_id": "s1",
                                       "text": "book 2027-12-10 14:00"})
        r1 = c.post("/simulate/text", json={"session_id": "s1", "text": "yes"})
        r2 = c.post("/simulate/text", json={"session_id": "s1", "text": "yes"})
        assert r1.json()["action"] == "booked"
        assert r2.json()["action"] == "reply"  # no pending → no double-book
    assert len(db.active()) == 1


# ---------------------------------------------------------------------------
# No-false-confirmation oracle: DB state never changes without explicit yes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", [
    "book 2027-12-01 09:00",
    "book 2027-12-02 10:00 60",
    "book 2027-12-03 15:30 15",
    "help",
    "book me in tomorrow",
    "what are your hours",
])
def test_no_booking_created_before_confirm(tmp_path, utterance):
    app, db = app_with_oracle(tmp_path, [])
    with TestClient(app) as c:
        c.post("/simulate/text", json={"session_id": "s1", "text": utterance})
    assert db.active() == [], \
        f"Booking created before confirm for utterance: {utterance!r}"


# ---------------------------------------------------------------------------
# Two-call same-slot oracle
# ---------------------------------------------------------------------------

def test_two_calls_same_slot_one_booking(tmp_path):
    """Two calls attempting the same slot: exactly one booking, no duplicate."""
    db_path = str(tmp_path / "two_calls.sqlite")
    results = {}

    def call(call_num):
        a = create_app(
            Settings(db_path=db_path, dev_allow_unsigned=True,
                     wss_url="wss://localhost/twilio/media"),
            asr_factory=lambda: MockASR(["book 2027-12-10 14:00", "yes"]),
            tts=MockTTS(),
        )
        with TestClient(a) as c:
            r1 = c.post("/simulate/text",
                        json={"session_id": f"c{call_num}",
                              "text": "book 2027-12-10 14:00"})
            r2 = c.post("/simulate/text",
                        json={"session_id": f"c{call_num}", "text": "yes"})
            return r2.json()["action"]

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(call, 1)
        f2 = pool.submit(call, 2)
        a1, a2 = f1.result(), f2.result()

    statuses = {a1, a2}
    assert "booked" in statuses
    assert statuses <= {"booked", "slot_taken", "error"}
    assert len(BookingStore(db_path).active()) == 1


def test_two_calls_different_slots_both_book(tmp_path):
    """Two calls for different slots: both should succeed."""
    db_path = str(tmp_path / "two_diff.sqlite")

    def call(slot, call_num):
        a = create_app(
            Settings(db_path=db_path, dev_allow_unsigned=True,
                     wss_url="wss://localhost/twilio/media"),
            asr_factory=lambda: MockASR([f"book {slot}", "yes"]),
            tts=MockTTS(),
        )
        with TestClient(a) as c:
            c.post("/simulate/text",
                   json={"session_id": f"c{call_num}", "text": f"book {slot}"})
            return c.post("/simulate/text",
                          json={"session_id": f"c{call_num}", "text": "yes"}
                          ).json()["action"]

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(call, "2027-12-10 14:00", 1)
        f2 = pool.submit(call, "2027-12-10 15:00", 2)
        a1, a2 = f1.result(), f2.result()

    assert a1 == "booked"
    assert a2 == "booked"
    assert len(BookingStore(db_path).active()) == 2


# ---------------------------------------------------------------------------
# Cross-session state isolation oracle
# ---------------------------------------------------------------------------

def test_cross_session_pending_state_isolated(tmp_path):
    """Session 2 saying 'yes' must not commit session 1's pending booking."""
    app, db = app_with_oracle(tmp_path, [])
    with TestClient(app) as c:
        # Session 1 books but doesn't confirm
        c.post("/simulate/text", json={"session_id": "s1",
                                       "text": "book 2027-12-10 14:00"})
        # Session 2 says yes (no pending in s2)
        r = c.post("/simulate/text", json={"session_id": "s2", "text": "yes"})
        assert r.json()["action"] == "reply"  # no pending, no booking
        assert db.active() == []

        # Session 1 now confirms
        r = c.post("/simulate/text", json={"session_id": "s1", "text": "yes"})
        assert r.json()["action"] == "booked"
        assert len(db.active()) == 1
