"""The feels-human scoreboard counts exactly the robotic mistakes seen on real calls."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import scoreboard  # noqa: E402

DEAD = "Hmm, that's outside what I can help with. But I can answer questions about LexMakesIt."


def t(heard, reply, latency=1.0, gen=1):
    return {"gen": gen, "heard": heard, "reply": reply, "latency": latency}


def test_counts_each_robotic_pattern():
    s = scoreboard.score([
        t("Um...", DEAD),                                                     # answered a filler
        t("(grunting)", DEAD),                                                # answered a noise tag; same reply again
        t("What available appointments do you have?", "What day works for you?"),   # no times offered
        t("Hello, are you there?", "Okay.", latency=4.2),                     # dead air felt; slow
        t("Can you fix my printer?", DEAD),                                   # dead end on a real question
    ])
    assert s["answered_filler"] == 2 and s["repeated_reply"] == 1
    assert s["availability_without_times"] == 1 and s["caller_asked_are_you_there"] == 1
    assert s["slow_reply_over_3s"] == 1 and s["dead_end"] == 1


def test_a_good_turn_scores_zero():
    s = scoreboard.score([t("What times do you have tomorrow?",
                            "Tomorrow I have ten a m, eleven a m, or two p m. Which works best?")])
    assert s["robotic_total"] == 0


def test_reads_review_logs(tmp_path):
    f = tmp_path / "c.jsonl"
    f.write_text("\n".join(json.dumps(r) for r in [
        {"type": "start"}, {"type": "turn", "gen": 1, "heard": "hi", "reply": "Hi!"},
        {"type": "latency", "gen": 1, "end_of_speech_to_audio_s": 3.5}, {"type": "end"}]))
    (turn,) = scoreboard.turns_of(str(f))
    assert turn["latency"] == 3.5
