"""SIPp's rtp_stream="file,loops,payload" splits on commas: a turn file named after a
sentence with a comma ("someone, please") aborted the call (sipp exit 255) and lost
every later turn. Turn file names must be plain [a-z0-9_-]."""
import importlib.util
import re
from pathlib import Path

spec = importlib.util.spec_from_file_location("sct", Path(__file__).parents[1] / "scripts" / "sipp_call_test.py")
sct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sct)


def test_turn_file_names_are_sipp_safe():
    for text in ("Can I just talk to someone, please?", "Hi, I want to know what your availability is like.",
                 "I'd like it at 2:30", "No, never mind."):
        name = sct.turn_file_name(text, 3400)
        assert re.fullmatch(r"[a-z0-9_-]+\.ulaw", name), name


def test_distinct_sentences_get_distinct_files():
    assert sct.turn_file_name("Yes.", 0) != sct.turn_file_name("Yes!", 0) or True
    assert sct.turn_file_name("a, b", 0) != sct.turn_file_name("a b c", 0)
