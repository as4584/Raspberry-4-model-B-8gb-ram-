"""Owner-approved review mode (2026-11-21): calls from numbers in REVIEW_CALLERS (the
owner's own phone) get a transcript + per-turn decisions/timings (Python) and a stereo
recording (Asterisk MixMonitor). Every other caller stays unrecorded. 14-day retention."""
import asyncio
import json
import os
import re
from pathlib import Path

from receptionist.audio import MockASR, MockTTS
from receptionist.booking import BookingStore
from receptionist.telephony import AudioIn, CallEngine, CallStart, review_key

ROOT = Path(__file__).resolve().parents[1]
EXT = (ROOT / "deploy" / "asterisk" / "extensions.conf").read_text()
LOUD, QUIET = b"\x00" * 160, b"\xff" * 160


class T:
    def __init__(self, script): self.script, self.sent = script, []
    async def events(self):
        for i in self.script:
            if isinstance(i, float):
                await asyncio.sleep(i)
            else:
                yield i
    async def send_frame(self, f): pass
    async def flush(self): pass
    async def mark(self, n): pass
    async def hangup(self): pass
    async def transfer(self, t): pass


def call(tmp_path, caller):
    eng = CallEngine(BookingStore(str(tmp_path / "r.sqlite")), asr=MockASR(["book 2027-12-10 14:00"]),
                     tts=MockTTS(), review_dir=str(tmp_path / "review"), review_callers={"9736530992"})
    asyncio.run(eng.run(T([CallStart("C1", caller), *[AudioIn(LOUD)] * 10, *[AudioIn(QUIET)] * 20, 0.6])))
    return list((tmp_path / "review").glob("*.jsonl")) if (tmp_path / "review").exists() else []


def test_review_key_matches_any_us_format():
    assert review_key("9736530992") == review_key("+19736530992") == review_key("19736530992") == "9736530992"
    assert review_key(None) is None


def test_owner_call_gets_transcript_decision_and_timing(tmp_path):
    (f,) = call(tmp_path, "+19736530992")
    rows = [json.loads(l) for l in f.read_text().splitlines()]
    turn = next(r for r in rows if r.get("type") == "turn")
    assert turn["heard"] == "book 2027-12-10 14:00" and turn["action"] == "confirm_required"
    assert turn["reply"].startswith("Okay, let me check that.") and "asr_s" in turn
    if os.name != "nt":
        assert oct(f.stat().st_mode & 0o777) == "0o600"


def test_other_callers_are_never_recorded(tmp_path):
    assert call(tmp_path, "+15550000001") == [] and call(tmp_path, None) == []


def test_dialplan_records_only_review_callers_and_retention_is_set():
    rec = EXT[EXT.index("[receptionist]"):].split("\n[")[0]
    assert "#tryinclude /etc/asterisk/review.conf" in EXT
    assert 'GotoIf($["${GLOBAL(REVIEW_${CALLERID(num):-10})}" != "1"]?go)' in rec
    assert "MixMonitor(/var/spool/asterisk/review/${STRFTIME(,,%Y%m%d-%H%M%S)}-${UNIQUEID}.raw,D)" in rec
    timer = (ROOT / "deploy" / "pi-receptionist-review-cleanup.service").read_text()
    assert "-mtime +14 -delete" in timer and "/var/spool/asterisk/review" in timer
