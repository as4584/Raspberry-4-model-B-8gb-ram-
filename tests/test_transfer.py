"""T6: transfer to a human. The receptionist never bridges calls itself: it sets
TRANSFER_TO on the caller's channel via ARI (loopback only), hangs up its own
WebSocket leg, and the dialplan dials the human. No target configured -> no transfer."""
import asyncio
import re
from pathlib import Path

from receptionist.asterisk_transport import AsteriskWebSocketTransport
from receptionist.audio import MockASR, MockTTS
from receptionist.booking import BookingStore
from receptionist.telephony import AudioIn, CallEngine, CallStart

EXT = (Path(__file__).resolve().parents[1] / "deploy" / "asterisk" / "extensions.conf").read_text()
LOUD, QUIET = b"\x00" * 160, b"\xff" * 160


def test_dialplan_passes_channel_and_continues_to_transfer():
    assert "v(caller=${URIENCODE(${CALLERID(num)})},chan=${UNIQUEID})" in EXT
    assert "chan=${UNIQUEID}),,g)" in EXT                      # 'g': continue after our hangup
    assert 'GotoIf($["${TRANSFER_TO}" != ""]?transfer)' in EXT
    assert "Dial(PJSIP/${TRANSFER_TO}@telnyx,30)" in EXT


class FakeARI:
    def __init__(self): self.calls = []
    async def set_variable(self, channel, name, value): self.calls.append((channel, name, value))


class FakeWS:
    def __init__(self): self.sent = []
    async def send_text(self, t): self.sent.append(t)


def test_transport_transfer_sets_variable_on_caller_channel_then_hangs_up():
    ari, ws = FakeARI(), FakeWS()
    t = AsteriskWebSocketTransport(ws, ari=ari)
    t.caller_channel = "1763500000.1"
    asyncio.run(t.transfer("+15557654321"))
    assert ari.calls == [("1763500000.1", "TRANSFER_TO", "+15557654321")]
    assert ws.sent and '"HANGUP"' in ws.sent[-1]


class Recorder:
    def __init__(self, script): self.script, self.sent = script, []
    async def events(self):
        for i in self.script:
            if isinstance(i, float):
                await asyncio.sleep(i)
            else:
                yield i
    async def send_frame(self, f): self.sent.append("audio")
    async def flush(self): self.sent.append("flush")
    async def mark(self, n): self.sent.append(("mark", n))
    async def hangup(self): self.sent.append("hangup")
    async def transfer(self, target): self.sent.append(("transfer", target))


def speech():
    return [AudioIn(LOUD)] * 10 + [AudioIn(QUIET)] * 20


def run(tmp_path, transfer_to):
    eng = CallEngine(BookingStore(str(tmp_path / "t.sqlite")), asr=MockASR(["i want to speak to a human"]),
                     tts=MockTTS(), transfer_to=transfer_to)
    r = Recorder([CallStart("C1", "+15550000001"), *speech(), 0.5])
    asyncio.run(eng.run(r))
    return r.sent


def test_handoff_transfers_after_speaking_when_a_target_is_configured(tmp_path):
    sent = run(tmp_path, "+15557654321")
    assert sent[-1] == ("transfer", "+15557654321")
    assert sent.index(("mark", "answer-1")) < sent.index(("transfer", "+15557654321"))


def test_handoff_without_target_only_speaks(tmp_path):
    assert not any(isinstance(s, tuple) and s[0] == "transfer" for s in run(tmp_path, None))


def test_service_reads_ari_secret_and_transfer_target_from_root_only_files():
    unit = (Path(__file__).resolve().parents[1] / "deploy" / "pi-receptionist.service").read_text().splitlines()
    assert "EnvironmentFile=-/etc/pi-receptionist/ari.env" in unit
    assert "EnvironmentFile=-/etc/pi-receptionist/telephony.env" in unit
