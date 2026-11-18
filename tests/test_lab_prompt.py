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
