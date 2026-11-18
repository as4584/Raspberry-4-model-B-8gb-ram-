"""S5 against a REAL uvicorn server (one event loop, like the Pi service), not
TestClient. Found on the PI4 2026-11-18: the deployed service logged
"No supported WebSocket library detected" and /twilio/media upgrades got 404,
so no Twilio call could connect, yet every TestClient test passed."""
import base64
import json
import socket
import threading
import time

import pytest
import uvicorn

from receptionist.audio import MockASR, MockTTS
from receptionist.booking import BookingStore
from receptionist.server import Settings, create_app

pytestmark = pytest.mark.timeout(60)
SLOW_S = 2.0


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    def __init__(self, app):
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("uvicorn did not start")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)


def connect(port):
    from websockets.sync.client import connect as ws_connect   # the dependency under test
    return ws_connect(f"ws://127.0.0.1:{port}/twilio/media", open_timeout=5)


def send(ws, obj):
    ws.send(json.dumps(obj))


def recv(ws, timeout=10):
    return json.loads(ws.recv(timeout=timeout))


def utterance(ws, sid):
    for _ in range(5):
        send(ws, {"event": "media", "streamSid": sid, "media": {"payload": base64.b64encode(bytes([0] * 160)).decode()}})
    for _ in range(16):
        send(ws, {"event": "media", "streamSid": sid, "media": {"payload": base64.b64encode(bytes([255] * 160)).decode()}})


def make_app(tmp_path, scripts):
    it = iter(scripts)
    return create_app(Settings(db_path=str(tmp_path / "r.sqlite"), dev_allow_unsigned=True,
                               wss_url="wss://localhost/twilio/media"),
                      asr_factory=lambda: MockASR(next(it)), tts=MockTTS())


def test_real_server_accepts_media_websocket_and_answers(tmp_path):
    with Server(make_app(tmp_path, [["book 2027-12-10 14:00"]])) as s, connect(s.port) as ws:
        send(ws, {"event": "start", "streamSid": "M1", "start": {"callSid": "C1"}})
        utterance(ws, "M1")
        events = [recv(ws)["event"] for _ in range(4)]
        assert events == ["clear", "media", "media", "mark"]


def test_slow_booking_on_one_call_does_not_freeze_another(tmp_path, monkeypatch):
    started = threading.Event()
    real_book = BookingStore.book

    def slow_book(self, *a, **k):
        started.set()
        time.sleep(SLOW_S)                       # simulates a held SQLite lock
        return real_book(self, *a, **k)

    monkeypatch.setattr(BookingStore, "book", slow_book)
    app = make_app(tmp_path, [["book 2027-12-10 14:00", "yes"], ["hello"]])
    with Server(app) as s, connect(s.port) as a, connect(s.port) as b:
        send(a, {"event": "start", "streamSid": "MA", "start": {"callSid": "CA"}})
        send(b, {"event": "start", "streamSid": "MB", "start": {"callSid": "CB"}})
        utterance(a, "MA")
        for _ in range(4):
            recv(a)                              # confirm prompt
        utterance(a, "MA")                       # "yes" -> slow book()
        assert started.wait(10), "booking never started"
        t0 = time.monotonic()
        utterance(b, "MB")
        first = recv(b)
        waited = time.monotonic() - t0
        assert first["event"] == "clear" and first["streamSid"] == "MB"
        assert waited < SLOW_S / 2, f"caller B blocked {waited:.2f}s behind caller A's booking"


def test_websocket_library_is_a_runtime_dependency():
    """Installing the package alone (as the Pi service does) must pull it in."""
    import tomllib
    from pathlib import Path
    deps = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(d.startswith(("websockets", "uvicorn[standard]")) for d in deps), deps
