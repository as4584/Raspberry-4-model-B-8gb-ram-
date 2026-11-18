"""Prompt/messages builder shared by baseline eval and fine-tuning (lab/eval/prompt.py)."""
import json

from lab.eval import prompt
from lab.eval.parse import parse_output
from lab.eval.schema import TOOL_NAMES

REC = {"text": "book me tomorrow at 3pm", "reference_now": "2026-11-18T10:00:00-05:00",
       "timezone": "America/New_York",
       "expected": {"name": "book_appointment", "arguments": {"date": "2026-11-19", "time": "15:00", "duration_min": 30}}}


def test_tools_cover_schema_and_are_function_specs():
    tools = prompt.tool_specs()
    assert [t["function"]["name"] for t in tools] == list(TOOL_NAMES)
    for t in tools:
        assert t["type"] == "function" and t["function"]["parameters"]["type"] == "object"


def test_system_prompt_states_rules_and_now():
    s = prompt.system_prompt(REC)
    assert "Wednesday 2026-11-18 10:00" in s
    assert "America/New_York" in s
    for must in ("exactly one tool call", "never", "09:00", "17:00", "Sunday", "handoff_to_human", "ask_clarification"):
        assert must in s, must


def test_messages_and_target_round_trip():
    msgs = prompt.messages(REC)
    assert [m["role"] for m in msgs] == ["system", "user"] and msgs[1]["content"] == REC["text"]
    target = prompt.target(REC)
    assert target.startswith("<tool_call>") and parse_output(target) == REC["expected"]


def test_few_shot_inserts_pairs_before_user():
    shots = [REC, {**REC, "text": "cancel please"}]
    msgs = prompt.messages(REC, shots=shots)
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user", "assistant", "user"]


def test_prompt_hash_is_stable_and_sensitive():
    h = prompt.prompt_hash()
    assert h == prompt.prompt_hash() and len(h) == 64
    assert prompt.prompt_hash(shots=[REC]) != h


def test_target_uses_qwen_name_first_order():
    assert prompt.target(REC).startswith('<tool_call>\n{"name": "book_appointment", "arguments": {"date"')


def test_json_compact_and_sorted():
    assert json.loads(prompt.target(REC)[len("<tool_call>\n"):-len("\n</tool_call>")]) == REC["expected"]


# --- v2 layout: cacheable prefix (latency) -------------------------------------
# v1 puts "Now: <minute>" on line 2 of the system prompt, so the ~1,100-token
# system+tools prefix changes every minute and llama-server's prompt cache can
# never reuse it. v2 moves Now into the user turn; the prefix is then constant.

LATER = {**REC, "reference_now": "2027-03-02T16:45:00-05:00", "text": "cancel booking 4729"}


def test_v2_system_prompt_is_identical_for_any_now():
    a, b = prompt.messages(REC, layout="v2"), prompt.messages(LATER, layout="v2")
    assert a[0] == b[0]
    assert "Now:" not in a[0]["content"] and "2026" not in a[0]["content"]
    for must in ("exactly one tool call", "never", "09:00", "17:00", "Sunday", "handoff_to_human", "ask_clarification"):
        assert must in a[0]["content"], must


def test_v2_user_turn_carries_now_then_caller_text():
    msgs = prompt.messages(REC, layout="v2")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[1]["content"] == "Now: Wednesday 2026-11-18 10:00 (America/New_York).\nCaller: book me tomorrow at 3pm"


def test_v2_shots_each_carry_their_own_now():
    msgs = prompt.messages(REC, shots=[LATER], layout="v2")
    assert msgs[1]["content"].startswith("Now: Tuesday 2027-03-02 16:45")
    assert msgs[3]["content"].startswith("Now: Wednesday 2026-11-18 10:00")


def test_v1_is_default_and_unchanged():
    assert prompt.messages(REC) == prompt.messages(REC, layout="v1")
    assert "Wednesday 2026-11-18 10:00" in prompt.messages(REC)[0]["content"]


def test_prompt_hash_differs_by_layout():
    assert prompt.prompt_hash(layout="v2") != prompt.prompt_hash()


def test_unknown_layout_rejected():
    import pytest
    with pytest.raises(ValueError):
        prompt.messages(REC, layout="v3")
