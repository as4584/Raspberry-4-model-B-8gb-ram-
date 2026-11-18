"""Real-voice round trip on the Pi (S4/G4 evidence): Piper speaks a caller line
-> 8 kHz u-law frames (as Twilio sends) -> real uvicorn /twilio/media with
VOICE_BACKEND=local (VAD -> whisper.cpp -> Dialogue -> Piper) -> reply audio ->
whisper again, to check what the receptionist actually SAID.

    WHISPER_CLI=... WHISPER_MODEL=... PIPER_CLI=... PIPER_MODEL=... \
      python scripts/voice_roundtrip.py "book december tenth at two pm"

Dev-only: runs its own unsigned server on 127.0.0.1 in a child process with a
throwaway DB. Latency printed is end-of-caller-speech -> first reply audio
frame; it is NOT valid evidence while the Pi is throttled (it prints the state)."""
import asyncio
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time

SILENCE = bytes([0xFF]) * 160


def _serve(db, port, ready):
    import logging
    logging.basicConfig(level=logging.WARNING, format="SERVER %(message)s")
    logging.getLogger("receptionist.call").setLevel(logging.INFO)
    import uvicorn
    from receptionist.audio import PiperTTS, WhisperCppASR
    from receptionist.server import Settings, create_app
    app = create_app(Settings(db_path=db, dev_allow_unsigned=True, wss_url="wss://localhost/twilio/media"),
                     asr_factory=lambda: WhisperCppASR(os.environ["WHISPER_CLI"], os.environ["WHISPER_MODEL"]),
                     tts=PiperTTS(os.environ["PIPER_CLI"], os.environ["PIPER_MODEL"]))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    def signal_ready():
        while not server.started:
            time.sleep(0.05)
        ready.set()

    threading.Thread(target=signal_ready, daemon=True).start()
    server.run()


def throttled():
    try:
        return subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout.strip()
    except OSError:
        return "unknown"


def main(argv):
    import multiprocessing as mp
    from websockets.sync.client import connect
    from receptionist.audio import PiperTTS, WhisperCppASR
    lines = argv[1:] or ["book december tenth at two pm"]
    piper = PiperTTS(os.environ["PIPER_CLI"], os.environ["PIPER_MODEL"])
    asr = WhisperCppASR(os.environ["WHISPER_CLI"], os.environ["WHISPER_MODEL"])
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    db = os.path.join(tempfile.mkdtemp(prefix="voice-"), "v.sqlite")
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    proc = ctx.Process(target=_serve, args=(db, port, ready), daemon=True)
    proc.start()
    if not ready.wait(60):
        proc.kill()
        raise SystemExit("server did not start")
    turns = []
    try:
        with connect(f"ws://127.0.0.1:{port}/twilio/media", open_timeout=10) as ws:
            ws.send(json.dumps({"event": "start", "streamSid": "MV", "start": {"callSid": "CV",
                                "customParameters": {"callerId": "+15550000001"}}}))
            for line in lines:                       # one call, several spoken turns
                caller = asyncio.run(piper.synthesize(line))
                frames = [caller[i:i + 160] for i in range(0, len(caller), 160)] + [SILENCE] * 25
                for f in frames:
                    ws.send(json.dumps({"event": "media", "streamSid": "MV", "media": {"payload": base64.b64encode(f).decode()}}))
                t_end, t_first, reply = time.monotonic(), None, bytearray()
                while True:
                    evt = json.loads(ws.recv(timeout=240))
                    if evt["event"] == "media":
                        t_first = t_first or time.monotonic()
                        reply += base64.b64decode(evt["media"]["payload"])
                    elif evt["event"] == "mark":
                        break
                turns.append({"caller_line": line, "whisper_heard_reply": asyncio.run(asr.transcribe(bytes(reply))),
                              "reply_audio_s": round(len(reply) / 8000, 2),
                              "end_of_speech_to_first_reply_audio_s": round(t_first - t_end, 2) if t_first else None})
            ws.send(json.dumps({"event": "stop", "streamSid": "MV"}))
    finally:
        proc.kill()
    import sqlite3
    rows = sqlite3.connect(db).execute("SELECT starts, ends, caller_id, status FROM bookings").fetchall()
    result = {"turns": turns, "bookings_in_db": rows, "throttled": throttled()}
    print(json.dumps(result, indent=1))
    ok = bool(turns) and all(t["reply_audio_s"] > 1 and t["whisper_heard_reply"].strip() for t in turns)
    print("VOICE_ROUNDTRIP_OK" if ok else "VOICE_ROUNDTRIP_FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
