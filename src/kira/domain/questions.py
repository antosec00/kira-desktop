"""Bounded information questions. These never authorize actions or permissions."""

import json
import re
from typing import Any, NoReturn

from kira.domain.contracts import Json, KiraError

MAX_INPUT_REQUESTS = 16
MAX_INPUT_BYTES = 65_536
MAX_ANSWER_BYTES = 16_384


def _fail(message: str) -> NoReturn:
    raise KiraError("INVALID_ARGUMENT", message)


def question_tool_specs() -> Json:
    """Provider-neutral schema for durable, non-authorizing information questions."""
    question = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "header": {"type": "string"},
            "question": {"type": "string"},
            "isOther": {"type": "boolean"},
            "options": {"type": "array", "maxItems": 6, "items": {
                "type": "object", "properties": {
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                }, "required": ["label"], "additionalProperties": False}},
        },
        "required": ["id", "question"], "additionalProperties": False,
    }
    return {"type": "namespace", "name": "questions",
            "description": "Persistent user questions; never permission or credential requests.",
            "tools": [
                {"type": "function", "name": "request_input",
                 "description": "Ask 1-3 structured questions. Each item in questions requires an ASCII id and question text, with optional options:[{label}].",
                 "inputSchema": {"type": "object", "properties": {
                     "blocking": {"type": "boolean"}, "questions": {"type": "array",
                         "items": question, "minItems": 1, "maxItems": 3}},
                     "required": ["blocking", "questions"], "additionalProperties": False}},
                {"type": "function", "name": "read_input",
                 "description": "Read a question answer by request_id (wait=true waits; pending is never approval).",
                 "inputSchema": {"type": "object", "properties": {
                     "request_id": {"type": "string"}, "wait": {"type": "boolean"}},
                     "required": ["request_id", "wait"], "additionalProperties": False}},
            ]}


def _bounded(value: Any, limit: int) -> None:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        _fail("Questions and answers must be finite UTF-8 JSON")
        return
    if len(encoded) > limit:
        _fail(f"Input exceeds the {limit} byte limit")


def _text(value: Any, name: str, maximum: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or "\0" in value or (not empty and not value.strip()):
        _fail(f"{name} must be nonempty plain text")
    assert isinstance(value, str)
    _bounded(value, maximum)
    return value


def validate_request(request: Json) -> Json:
    """Return a normalized copy without silently dropping unsupported input."""
    _bounded(request, MAX_INPUT_BYTES)
    if not isinstance(request, dict) or set(request) - {"request_id", "blocking", "questions"}:
        _fail("Unsupported input request fields")
    provider_id = _text(request.get("request_id"), "request_id", 512)
    if len(provider_id) > 256 or any(ord(c) < 32 for c in provider_id):
        _fail("request_id must be a bounded printable identifier")
    blocking = request.get("blocking")
    if type(blocking) is not bool:
        _fail("blocking must be a boolean")
    questions = request.get("questions")
    if not isinstance(questions, list) or not 1 <= len(questions) <= 3:
        _fail("Supply one to three questions")
    normalized: list[Json] = []
    identities: set[str] = set()
    for question in questions:
        if not isinstance(question, dict) or set(question) - {
                "id", "header", "question", "options", "isOther", "isSecret"}:
            _fail("Unsupported question fields")
        if question.get("isSecret", False) is not False:
            _fail("Secret input is unsupported; use the provider's authentication flow")
        identity = question.get("id")
        if not isinstance(identity, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", identity) is None:
            _fail("Question id must be a bounded ASCII identifier")
        if identity in identities:
            _fail("Question ids must be unique")
        identities.add(identity)
        other = question.get("isOther", True)
        if type(other) is not bool:
            _fail("isOther must be a boolean")
        options = question.get("options", [])
        if options is None:
            options = []
        if not isinstance(options, list) or len(options) > 6:
            _fail("Supply at most six choices per question")
        choices: list[Json] = []
        labels: set[str] = set()
        for option in options:
            if not isinstance(option, dict) or set(option) - {"label", "description"}:
                _fail("Unsupported choice fields")
            label = _text(option.get("label"), "label", 1000)
            if label.strip() in labels:
                _fail("Choice labels must be distinct")
            labels.add(label.strip())
            choices.append({"label": label, "description": _text(
                option.get("description", ""), "description", 4000, empty=True)})
        normalized.append({"id": identity, "header": _text(question.get("header", ""),
            "header", 256, empty=True), "question": _text(question.get("question"),
            "question", 12_000), "options": choices, "isOther": other})
    result = {"request_id": provider_id, "blocking": blocking, "questions": normalized}
    _bounded(result, MAX_INPUT_BYTES)
    return result


def validate_answers(questions: list[Json], answers: Any) -> Json:
    """Exactly one explicit answer per question, no defaults or permission effects."""
    _bounded(answers, MAX_ANSWER_BYTES)
    if not isinstance(answers, dict) or set(answers) != {q["id"] for q in questions}:
        _fail("Answer every question using its exact id")
    result: Json = {}
    for question in questions:
        entry = answers[question["id"]]
        if not isinstance(entry, dict) or set(entry) != {"answers"}:
            _fail("Each answer must contain an answers array")
        values = entry["answers"]
        if not isinstance(values, list) or len(values) != 1:
            _fail("Choose exactly one answer per question")
        value = _text(values[0], "answer", MAX_ANSWER_BYTES)
        options = question.get("options", [])
        if options and not question.get("isOther", True) and value not in {
                option["label"] for option in options}:
            _fail("This question requires one of its offered choices")
        result[question["id"]] = {"answers": [value]}
    return result
