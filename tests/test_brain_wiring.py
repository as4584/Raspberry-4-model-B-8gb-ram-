"""Wiring the brain into live calls: the engine uses a per-call dialogue factory; the
server picks the tiny-model Brain when BRAIN_DIR holds a loadable model bundle and falls
back to the rule Dialogue otherwise; the service installs the optional brain runtime."""
import asyncio
from pathlib import Path

from receptionist.audio import MockASR, MockTTS
from receptionist.booking import BookingStore
from receptionist.telephony import AudioIn, CallEngine, CallStart

ROOT = Path(__file__).resolve().parents[1]
LOUD, QUIET = b"\x00" * 160, b"\xff" * 160


class Recorder:
    def __init__(self):
        self.calls = []
    def handle(self, text, session, caller_id=None):
        from receptionist.dialogue import Reply
        self.calls.append(text); return Reply("ok", "reply", speech="Okay.")


def test_engine_uses_the_dialogue_factory_per_call(tmp_path):
    made = []
    def factory():
        made.append(Recorder()); return made[-1]
    class T:
        async def events(self):
            yield CallStart("C1", None)
            for f in [AudioIn(LOUD)] * 10 + [AudioIn(QUIET)] * 20:
                yield f
            await asyncio.sleep(0.5)
        async def send_frame(self, f): pass
        async def flush(self): pass
        async def mark(self, n): pass
        async def hangup(self): pass
        async def transfer(self, t): pass
    eng = CallEngine(BookingStore(str(tmp_path / "w.sqlite")), asr=MockASR(["hello there"]), tts=MockTTS(),
                     dialogue_factory=factory)
    asyncio.run(eng.run(T()))
    assert len(made) == 1 and made[0].calls == ["hello there"]


def test_server_falls_back_to_rules_without_a_model(tmp_path):
    from receptionist.server import select_dialogue_factory
    store = BookingStore(str(tmp_path / "s.sqlite"))
    factory, kind = select_dialogue_factory({"BRAIN_DIR": str(tmp_path / "missing")}, store, False)
    assert kind == "rules" and type(factory()).__name__ == "Dialogue"
    factory, kind = select_dialogue_factory({}, store, False)
    assert kind == "rules"


def test_server_uses_the_brain_with_an_injected_intent_model(tmp_path):
    from receptionist.server import select_dialogue_factory
    class Fake:
        def classify(self, t): return "book", 0.9
    store = BookingStore(str(tmp_path / "s.sqlite"))
    factory, kind = select_dialogue_factory({"BRAIN_DIR": "x"}, store, False, load_intents=lambda d: Fake())
    assert kind == "brain" and type(factory()).__name__ == "Brain" and factory() is not factory()


def test_brain_runtime_is_an_optional_extra_installed_on_the_pi():
    py = (ROOT / "pyproject.toml").read_text()
    assert 'brain = ["onnxruntime' in py and "tokenizers" in py
    assert '$app[brain]' in (ROOT / "scripts" / "pi_install_service.sh").read_text()
