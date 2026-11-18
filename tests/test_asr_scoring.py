"""S6 (#28): scoring for the ASR sweep. Normalisation must make equivalent
spoken forms compare equal ("10th" == "tenth", "2 p.m." == "two pm") so WER
measures recognition errors, not formatting."""
import pytest

from receptionist.asr_eval import normalise, wer, is_runaway


@pytest.mark.parametrize("a,b", [
    ("Book December 10th at 2 p.m.", "book december tenth at two pm"),
    ("I'd like to cancel.", "i'd like to cancel"),
    ("BOOK, december 3rd at 9:30 am", "book december third at nine thirty am"),
    ("  Yes.  ", "yes"),
])
def test_normalise_equates_spoken_forms(a, b):
    assert normalise(a) == normalise(b)


def test_wer_basics():
    assert wer("book december tenth", "book december tenth") == 0.0
    assert wer("book december tenth", "book december") == pytest.approx(1 / 3)
    assert wer("book december tenth", "look december tenth extra") == pytest.approx(2 / 3)
    assert wer("", "") == 0.0
    assert wer("", "anything") == 1.0


def test_wer_uses_normalisation():
    assert wer("Book December 10th at 2 p.m.", "book december tenth at two pm") == 0.0


@pytest.mark.parametrize("hyp,ref,decode_runs,expect", [
    ("book december tenth", "book december tenth", 9, False),
    ("book december tenth book december tenth", "book december tenth", 9, True),   # repeated
    ("ok", "ok", 446, True),                                                       # decode loop
])
def test_runaway_detection(hyp, ref, decode_runs, expect):
    assert is_runaway(hyp, ref, decode_runs) is expect


# #28: digits glued to am/pm were dropped ("10am" -> "am"), scoring a correct
# transcript as wrong.
@pytest.mark.parametrize("text,want", [
    ("10am", "ten am"), ("2pm", "two pm"), ("2PM", "two pm"), ("at 9:30am", "at nine thirty am"),
    ("9:00a.m.", "nine am"), ("December 10th at 2pm", "december tenth at two pm"),
    ("11:45 p.m.", "eleven forty five pm")])
def test_normalise_digits_glued_to_ampm(text, want):
    assert normalise(text) == want


def test_wer_zero_for_spoken_vs_numeric_glued_form():
    assert wer("book december tenth at two p m", "Book December 10th at 2pm.") == 0.0
