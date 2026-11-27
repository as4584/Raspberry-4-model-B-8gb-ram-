"""A Spanish caller: once the brain hears Spanish, the rest of the call listens in Spanish
and speaks with the Spanish voice. English calls never touch either."""
import asyncio

from receptionist.booking import BookingStore
from receptionist.dialogue import Reply
from receptionist.telephony import AudioIn, CallEngine, CallStart
from tests.test_call_engine import LOUD, QUIET, FakeTransport


class ASR:
    def __init__(self, texts):
        self.texts, self.language, self.seen = list(texts), "en", []

    async def transcribe(self, b):
        self.seen.append(self.language)
        return self.texts.pop(0) if self.texts else ""


class Voice:
    def __init__(self):
        self.said = []

    async def synthesize(self, text):
        self.said.append(text)
        return b"\xff" * 160


class Brain:
    def handle(self, text, session, caller_id=None):
        if "hola" in text.lower():
            return Reply("Hola", "spanish", speech="Hola, gracias por llamar.")
        return Reply("ok", "reply", speech="Muy bien." if "gracias" in text else "Okay.")


def call(tmp_path, texts):
    asr, en, es = ASR(texts), Voice(), Voice()
    eng = CallEngine(BookingStore(str(tmp_path / "s.sqlite")), asr=asr, tts=en, tts_es=es,
                     dialogue_factory=lambda: Brain())
    script = [CallStart("C1", "+15550000001")]
    for _ in texts:
        script += [*[AudioIn(LOUD)] * 10, *[AudioIn(QUIET)] * 20, 0.6]
    asyncio.run(eng.run(FakeTransport(script)))
    return asr, en, es


def test_spanish_switches_voice_and_ears(tmp_path):
    asr, en, es = call(tmp_path, ["hola necesito", "gracias"])
    assert asr.seen == ["auto", "es"]       # first words: language detected; then Spanish
    assert "Hola, gracias por llamar." in es.said and "Muy bien." in es.said
    assert not any("Hola" in s for s in en.said)


def test_english_call_stays_english(tmp_path):
    asr, en, es = call(tmp_path, ["hello", "thanks"])
    assert asr.seen == ["auto", "en"] and not es.said


def test_next_call_starts_in_english_again(tmp_path):
    asr, en, es = ASR(["hola"]), Voice(), Voice()
    eng = CallEngine(BookingStore(str(tmp_path / "t.sqlite")), asr=asr, tts=en, tts_es=es,
                     dialogue_factory=lambda: Brain())
    s = [CallStart("C1", "+15550000001"), *[AudioIn(LOUD)] * 10, *[AudioIn(QUIET)] * 20, 0.6]
    asyncio.run(eng.run(FakeTransport(s)))
    asyncio.run(eng.run(FakeTransport([CallStart("C2", "+15550000002"), 0.1])))
    assert asr.language == "auto"          # a fresh call detects again, never stuck in Spanish


def test_spanish_voice_only_when_configured():
    from receptionist.server import select_tts_es
    assert select_tts_es({"VOICE_BACKEND": "local"}) is None
    assert select_tts_es({"VOICE_BACKEND": "local", "PIPER_MODEL_ES": "/m/es.onnx", "PIPER_RESIDENT": "1"}) is not None
