"""Strict validation of a proposed tool call against docs/models/tool_call.schema.json.

Dependency-free: a tiny interpreter for exactly the JSON-Schema subset the file uses
(oneOf, const, enum, type, pattern, required, additionalProperties:false, min/maxLength).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "docs" / "models" / "tool_call.schema.json"
ACTION_TOOLS = {"book_appointment", "reschedule_appointment", "cancel_appointment"}


@lru_cache(maxsize=1)
def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _alternatives() -> dict[str, dict]:
    return {alt["properties"]["name"]["const"]: alt for alt in load_schema()["oneOf"]}


TOOL_NAMES = tuple(_alternatives())

_TYPES = {"object": dict, "string": str, "null": type(None)}


def _type_ok(value, t) -> bool:
    for name in ([t] if isinstance(t, str) else t):
        if name == "integer":
            if isinstance(value, int) and not isinstance(value, bool):
                return True
        elif isinstance(value, _TYPES[name]):
            return True
    return False


def _check(value, s: dict, path: str) -> list[str]:
    errs: list[str] = []
    if "const" in s and (value != s["const"] or type(value) is not type(s["const"])):
        return [f"{path}: expected {s['const']!r}"]
    if "enum" in s and not any(value == e and type(value) is type(e) for e in s["enum"]):
        return [f"{path}: {value!r} not in {s['enum']}"]
    if "type" in s and not _type_ok(value, s["type"]):
        return [f"{path}: wrong type {type(value).__name__}"]
    if isinstance(value, str):
        if "pattern" in s and not re.search(s["pattern"], value):
            errs.append(f"{path}: {value!r} does not match {s['pattern']}")
        if len(value) < s.get("minLength", 0) or len(value) > s.get("maxLength", 10**9):
            errs.append(f"{path}: bad length")
    if isinstance(value, dict):
        props = s.get("properties", {})
        for key in s.get("required", []):
            if key not in value:
                errs.append(f"{path}.{key}: required")
        for key, v in value.items():
            if key not in props:
                if s.get("additionalProperties", True) is False:
                    errs.append(f"{path}.{key}: not allowed")
                continue
            errs += _check(v, props[key], f"{path}.{key}")
    return errs


def validate_call(call) -> list[str]:
    """Return a list of schema errors ([] means valid). Never raises."""
    if not isinstance(call, dict):
        return ["call is not an object"]
    alt = _alternatives().get(call.get("name"))
    if alt is None:
        return [f"unknown tool {call.get('name')!r}"]
    return _check(call, alt, "$")
