"""Two-way conversation lab: the live brain (same code, intent model, knowledge and business
profile as the Pi) answers one caller turn at a time, so a simulated caller can react to each
reply. With ears on, the caller's words go Piper -> 8 kHz u-law -> whisper base.en, the same
hearing a phone call gets. Bookings go to a throwaway database, never the real one.

Run on the Pi from a checkout (loopback only):
    WHISPER_CLI=... LD_LIBRARY_PATH=... PIPER_CLI=... PIPER_MODEL=... BRAIN_DIR=... \
        .venv/bin/python lab/sim/chat_server.py --port 8099

    POST /start {"session": "s1"}                         -> {"reply": greeting}
    POST /turn  {"session": "s1", "text": "...", "ears": true}
                                                          -> {"heard", "reply", "action", "ended"}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from receptionist.booking import BookingStore  # noqa: E402
from receptionist.server import load_business_profile, select_dialogue_factory  # noqa: E402

ENDS = {"goodbye", "end", "hangup"}
WHISPER = "/opt/pi-receptionist/speech/models/ggml-base.en-q5_1.bin"
WHISPER_ES = os.environ.get("WHISPER_MODEL_ES", "/home/prototype/models/es/ggml-base-q5_1.bin")
VOICE_ES = os.environ.get("PIPER_MODEL_ES", "/home/prototype/models/es/es_MX-claude-high.onnx")


class Lab:
    def __init__(self):
        self.db = Path(tempfile.mkdtemp(prefix="simlab-")) / "bookings.sqlite3"
        store = BookingStore(str(self.db))
        profile, self.greeting = load_business_profile(os.environ)
        self.factory, kind = select_dialogue_factory(os.environ, store, require_caller_id=False, profile=profile)
        if kind != "brain":
            raise SystemExit("brain bundle did not load (check BRAIN_DIR)")
        self.sessions: dict[str, object] = {}
        self.lock = threading.Lock()
        self.ears_lock = threading.Lock()
        self.heard_once: set[str] = set()      # one whisper run at a time on the Pi

    def hear(self, text: str, lang: str = "en") -> str:
        """Phone path. A call the brain has switched to Spanish is heard like production would:
        a Spanish caller voice, the multilingual model, language fixed to es."""
        import asr_sweep
        bilingual = os.path.exists(WHISPER_ES) and os.path.exists(VOICE_ES)
        # The simulated caller's own voice follows what they say.
        spanish_caller = bilingual and bool(re.search(r"[áéíóúñ¿¡]|\b(hola|necesito|quiero|gracias|cuánto|bueno|mira)\b", text, re.I))
        with self.ears_lock, tempfile.TemporaryDirectory() as d:
            mulaw = asr_sweep.synth(text, Path(d), VOICE_ES if spanish_caller else None)
            # Production with Spanish on: one multilingual model, first words auto-detected.
            model = WHISPER_ES if bilingual else WHISPER
            heard, *_ = asr_sweep.transcribe(model, mulaw, "adaptive", True, lang=lang if bilingual else "en")
        return heard

    def turn(self, session: str, text: str, ears: bool) -> dict:
        with self.lock:
            brain = self.sessions.setdefault(session, self.factory())
        # Mirrors the engine: detect until a real English turn, then fix the language.
        lang = getattr(brain, "_lang", {}).get(session) or ("en" if session in self.heard_once else "auto")
        heard = self.hear(text, lang) if ears else text
        t0 = time.monotonic()
        r = brain.handle(heard, session, caller_id="+15550001000")
        if r.action not in ("spanish", "unknown", "lost", "repeat", "filler", "ask_again", "confirm_again"):
            self.heard_once.add(session)
        return {"heard": heard, "reply": r.speech or r.text, "action": r.action,
                "ended": r.action in ENDS, "brain_s": round(time.monotonic() - t0, 3)}


def serve(lab: Lab, port: int):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if self.path == "/start":
                    with lab.lock:
                        lab.sessions.pop(body["session"], None)
                    out = {"reply": lab.greeting}
                elif self.path == "/turn":
                    out = lab.turn(body["session"], str(body["text"])[:500], bool(body.get("ears", True)))
                else:
                    self.send_error(404); return
                data, code = json.dumps(out).encode(), 200
            except Exception as exc:                      # report, keep serving
                data, code = json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode(), 500
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    serve(Lab(), ap.parse_args().port)
