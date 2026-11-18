"""Extract a single proposed tool call from raw model text. Returns None when there is none."""
from __future__ import annotations

import json
import re

_TOOL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_THINK = re.compile(r"<think>.*?</think>", re.S)


def _load(s: str):
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None
    if isinstance(obj, dict) and "name" in obj:
        if isinstance(obj.get("arguments"), str):  # some templates double-encode
            try:
                obj["arguments"] = json.loads(obj["arguments"])
            except json.JSONDecodeError:
                return None
        return obj
    return None


def parse_output(raw: str):
    text = _THINK.sub("", raw or "").strip()
    m = _TOOL.search(text)
    if m:
        return _load(m.group(1))
    m = _FENCE.search(text)
    if m:
        return _load(m.group(1).strip())
    if text.startswith("{"):
        return _load(text)
    return None
