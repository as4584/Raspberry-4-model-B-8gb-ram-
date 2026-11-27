"""Lead alerts: a saved message POSTs {name, callback, urgent, language} to the owner's own
NOTIFY_URL in the background. Never the caller's words, never blocks or breaks the call."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from receptionist.notify import webhook_notifier


def _server():
    got = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            got.append((self.headers.get("Authorization"), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(204); self.end_headers()

        def log_message(self, *a):
            pass
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, got


def test_off_without_url():
    assert webhook_notifier({}) is None


def test_posts_name_and_callback_only():
    srv, got = _server()
    n = webhook_notifier({"NOTIFY_URL": f"http://127.0.0.1:{srv.server_port}/lead", "NOTIFY_TOKEN": "t0k"})
    n({"name": "Kim", "callback": "2015550177", "urgent": True, "language": "en", "body": "secret words"})
    for _ in range(50):
        if got:
            break
        time.sleep(0.05)
    srv.shutdown()
    auth, payload = got[0]
    assert auth == "Bearer t0k"
    assert payload == {"name": "Kim", "callback": "2015550177", "urgent": True, "language": "en"}


def test_unreachable_url_never_raises():
    n = webhook_notifier({"NOTIFY_URL": "http://127.0.0.1:9/nothing"})
    t0 = time.time()
    n({"name": "x", "callback": "1"})
    assert time.time() - t0 < 0.5


def test_brain_reports_urgent_and_language(tmp_path):
    from receptionist.booking import BookingStore
    from receptionist.brain import Brain
    from tests.test_kindness import CALLER, NOW, FakeIntents
    seen = []
    b = Brain(BookingStore(str(tmp_path / "n.sqlite")), intents=FakeIntents(), clock=lambda: NOW, on_message=seen.append)
    for t in ("Can you take a message?", "Kim, 201 555 0177", "My site is down, it's urgent"):
        b.handle(t, "c", caller_id=CALLER)
    assert seen and seen[0]["urgent"] is True and seen[0]["language"] == "en" and "body" not in seen[0]
