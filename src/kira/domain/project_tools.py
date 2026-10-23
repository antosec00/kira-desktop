"""Bounded contracts for Kira's project-owned Python tool gateway."""

import hashlib
import json
import math
from pathlib import PurePosixPath
import re
from typing import Any

from kira.domain.contracts import Json, KiraError

PROJECT_TOOL_NAMES = frozenset({"kira.project_tools"})
PROJECT_TOOL_CALL_TIMEOUT = 240
MAX_TOOL_SECONDS = 60
MAX_TOOL_OUTPUT_BYTES = 32768
MAX_TEST_CASES = 3
ACTIONS = frozenset({"describe", "search", "read", "register", "test", "run"})
MAX_SOURCE_BYTES = 256 * 1024
MAX_MANIFEST_BYTES = 32 * 1024
MAX_ARGUMENT_BYTES = 32 * 1024
MAX_RESULT_BYTES = 48 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise KiraError("INVALID_ARGUMENT", "Project tool values must be finite JSON") from exc


def bounded_json(value: Any, maximum: int = MAX_ARGUMENT_BYTES, depth: int = 0) -> None:
    if depth > 16:
        raise KiraError("INVALID_ARGUMENT", "Project tool data exceeds its nesting limit")
    if isinstance(value, dict):
        if len(value) > 256 or any(not isinstance(key, str) or "\0" in key for key in value):
            raise KiraError("INVALID_ARGUMENT", "Project tool objects require bounded text keys")
        for item in value.values():
            bounded_json(item, maximum, depth + 1)
    elif isinstance(value, list):
        if len(value) > 256:
            raise KiraError("INVALID_ARGUMENT", "Project tool arrays exceed their limit")
        for item in value:
            bounded_json(item, maximum, depth + 1)
    elif value is not None and type(value) not in {str, int, float, bool}:
        raise KiraError("INVALID_ARGUMENT", "Project tool data must be JSON")
    if depth == 0:
        try:
            size = len(canonical(value).encode("utf-8"))
        except UnicodeError as exc:
            raise KiraError("INVALID_ARGUMENT", "Project tool text must be valid UTF-8") from exc
        if size > maximum:
            raise KiraError("OUTPUT_LIMIT", "Project tool JSON exceeds its size limit")


def text(value: Any, name: str, maximum: int = 128, *, empty: bool = False) -> str:
    if (not isinstance(value, str) or "\0" in value or len(value) > maximum
            or not empty and not value.strip()):
        raise KiraError("INVALID_ARGUMENT", f"{name} must be bounded text")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise KiraError("INVALID_ARGUMENT", f"{name} must be valid UTF-8") from exc
    return value


def integer(value: Any, name: str, low: int = 1, high: int = 2**31 - 1) -> int:
    if type(value) is not int or not low <= value <= high:
        raise KiraError("INVALID_ARGUMENT", f"{name} must be an integer between {low} and {high}")
    return value


def fields(value: Any, allowed: set[str], required: set[str] | None = None) -> Json:
    if not isinstance(value, dict) or set(value) - allowed or (required or set()) - set(value):
        raise KiraError("INVALID_ARGUMENT", "Unknown or missing project tool fields")
    return value


def source_path(value: Any) -> str:
    path = text(value, "source_path", 2048)
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts or not parsed.parts or "\\" in path:
        raise KiraError("PATH_DENIED", "Tool source must be a project-relative file")
    if parsed.suffix != ".py":
        raise KiraError("INVALID_ARGUMENT", "Project tools require one Python source file")
    return parsed.as_posix()


def validate_schema(value: Any, depth: int = 0) -> Json:
    if depth > 4:
        raise KiraError("INVALID_ARGUMENT", "Tool schemas exceed their nesting limit")
    fields(value, {"type", "description", "properties", "required", "additionalProperties", "items",
                   "enum", "minLength", "maxLength", "minimum", "maximum", "minItems", "maxItems"}, {"type"})
    kind = value["type"]
    if not isinstance(kind, str) or kind not in {"object", "array", "string", "integer", "number", "boolean", "null"}:
        raise KiraError("INVALID_ARGUMENT", "Unsupported tool schema type")
    allowed = {"type", "description", "enum"} | {
        "object": {"properties", "required", "additionalProperties"},
        "array": {"items", "minItems", "maxItems"}, "string": {"minLength", "maxLength"},
        "integer": {"minimum", "maximum"}, "number": {"minimum", "maximum"},
        "boolean": set(), "null": set(),
    }[kind]
    fields(value, allowed)
    result = dict(value)
    if "description" in result:
        text(result["description"], "schema description", 500, empty=True)
    if "enum" in result:
        if not isinstance(result["enum"], list) or not 1 <= len(result["enum"]) <= 32:
            raise KiraError("INVALID_ARGUMENT", "Tool schema enum exceeds its limit")
        bounded_json(result["enum"], 4096)
    if kind == "object":
        properties = value.get("properties", {})
        if not isinstance(properties, dict) or len(properties) > 32:
            raise KiraError("INVALID_ARGUMENT", "Tool schemas permit at most 32 properties")
        result["properties"] = {text(key, "property", 80): validate_schema(item, depth + 1)
                                for key, item in properties.items()}
        required = value.get("required", [])
        if (not isinstance(required, list) or any(not isinstance(key, str) for key in required)
                or len(required) != len(set(required)) or not set(required) <= properties.keys()
                or value.get("additionalProperties", False) is not False):
            raise KiraError("INVALID_ARGUMENT", "Tool object schemas must be closed")
        result.update(required=required, additionalProperties=False)
    elif kind == "array":
        result["items"] = validate_schema(value.get("items"), depth + 1)
        result["minItems"] = integer(value.get("minItems", 0), "minItems", 0, 256)
        result["maxItems"] = integer(value.get("maxItems", 128), "maxItems", result["minItems"], 256)
    elif kind == "string":
        result["minLength"] = integer(value.get("minLength", 0), "minLength", 0, 16384)
        result["maxLength"] = integer(value.get("maxLength", 4096), "maxLength", result["minLength"], 16384)
    elif kind in {"integer", "number"}:
        for key in ("minimum", "maximum"):
            if key in value and (type(value[key]) not in {int, float}
                                 or type(value[key]) is float and not math.isfinite(value[key])):
                raise KiraError("INVALID_ARGUMENT", "Tool numeric bounds must be finite numbers")
        if value.get("minimum", -math.inf) > value.get("maximum", math.inf):
            raise KiraError("INVALID_ARGUMENT", "Tool numeric bounds are inverted")
    if depth == 0:
        bounded_json(result, 8192)
    return result


def validate_value(value: Any, schema: Json) -> None:
    kind = schema["type"]
    matches = {"object": isinstance(value, dict), "array": isinstance(value, list),
        "string": isinstance(value, str), "integer": type(value) is int,
        "number": type(value) in {int, float}, "boolean": type(value) is bool, "null": value is None}
    if not matches[kind]:
        raise KiraError("SCHEMA_MISMATCH", "Tool value has the wrong JSON type")
    if "enum" in schema and canonical(value) not in {canonical(item) for item in schema["enum"]}:
        raise KiraError("SCHEMA_MISMATCH", "Tool value is outside its declared enum")
    if kind == "object":
        if set(value) - schema["properties"].keys() or set(schema["required"]) - set(value):
            raise KiraError("SCHEMA_MISMATCH", "Tool object has unexpected or missing fields")
        for key, item in value.items():
            validate_value(item, schema["properties"][key])
    elif kind == "array":
        if not schema["minItems"] <= len(value) <= schema["maxItems"]:
            raise KiraError("SCHEMA_MISMATCH", "Tool array length is outside its bounds")
        for item in value:
            validate_value(item, schema["items"])
    elif kind == "string":
        if not schema["minLength"] <= len(value) <= schema["maxLength"]:
            raise KiraError("SCHEMA_MISMATCH", "Tool text length is outside its bounds")
    elif kind in {"integer", "number"}:
        if (type(value) is float and not math.isfinite(value)
                or not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf)):
            raise KiraError("SCHEMA_MISMATCH", "Tool number is outside its bounds")


def validate_manifest(value: Any) -> Json:
    fields(value, {"name", "description", "input_schema", "output_schema", "tests",
                   "timeout_seconds", "max_output_bytes"},
           {"name", "description", "input_schema", "output_schema", "tests"})
    name = text(value["name"], "name", 48)
    if re.fullmatch(r"[a-z][a-z0-9_]*", name) is None:
        raise KiraError("INVALID_ARGUMENT", "Tool names use lowercase letters, digits and underscores")
    result: Json = {"name": name, "description": text(value["description"], "description", 1200),
        "input_schema": validate_schema(value["input_schema"]),
        "output_schema": validate_schema(value["output_schema"]),
        "timeout_seconds": integer(value.get("timeout_seconds", 10), "timeout_seconds", 1, MAX_TOOL_SECONDS),
        "max_output_bytes": integer(value.get("max_output_bytes", 16384), "max_output_bytes", 1024,
                                    MAX_TOOL_OUTPUT_BYTES)}
    if result["input_schema"]["type"] != "object":
        raise KiraError("INVALID_ARGUMENT", "Project tool input must be a JSON object")
    tests = value["tests"]
    if not isinstance(tests, list) or not 1 <= len(tests) <= MAX_TEST_CASES:
        raise KiraError("INVALID_ARGUMENT", "Declare between one and three tool tests")
    normalized = []
    for case in tests:
        fields(case, {"name", "arguments", "expected"}, {"name", "arguments", "expected"})
        text(case["name"], "test name", 80)
        bounded_json(case["arguments"], 8192)
        bounded_json(case["expected"], 8192)
        validate_value(case["arguments"], result["input_schema"])
        validate_value(case["expected"], result["output_schema"])
        normalized.append(dict(case))
    if len({case["name"] for case in normalized}) != len(normalized):
        raise KiraError("INVALID_ARGUMENT", "Tool test names must be unique")
    result["tests"] = normalized
    bounded_json(result, MAX_MANIFEST_BYTES)
    return result


def manifest_schema() -> Json:
    schema = {"type": "object", "description": (
        "Closed JSON-schema subset, at most 8192 UTF-8 bytes and nesting depth 4. "
        "Every node requires type: object|array|string|integer|number|boolean|null. "
        "Optional description (500 chars) and enum (1..32 finite JSON entries, 4096 bytes). "
        "Objects: properties (at most 32 keys of 80 chars), required (unique property names), "
        "additionalProperties:false. Arrays: required items schema, minItems/maxItems 0..256 (defaults 0/128). "
        "Strings: minLength/maxLength 0..16384 (defaults 0/4096). Numbers: finite minimum/maximum. "
        "Only keywords for that node type are accepted. No refs, formats, regex or combinators.")}
    return {"type": "object", "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$", "maxLength": 48},
            "description": {"type": "string", "minLength": 1, "maxLength": 1200},
            "input_schema": {**schema, "description": schema["description"] + " Root type must be object."},
            "output_schema": schema,
            "tests": {"type": "array", "minItems": 1, "maxItems": MAX_TEST_CASES,
                "items": {"type": "object", "additionalProperties": False,
                    "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 80},
                        "arguments": {"type": "object", "description": "Matches input_schema; at most 8192 bytes."},
                        "expected": {"description": "Matches output_schema; at most 8192 bytes. Exact JSON comparison."}},
                    "required": ["name", "arguments", "expected"]}},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_TOOL_SECONDS, "default": 10},
            "max_output_bytes": {"type": "integer", "minimum": 1024, "maximum": MAX_TOOL_OUTPUT_BYTES,
                                 "default": 16384}},
        "required": ["name", "description", "input_schema", "output_schema", "tests"],
        "description": "At most 32768 UTF-8 bytes total. Unique nonblank test names. Finite JSON only."}


def action_schemas() -> Json:
    identifier = {"type": "string", "maxLength": 128}
    revision = {"type": "integer", "minimum": 1, "maximum": 2**31 - 1}
    def obj(properties: Json, required: list[str]) -> Json:
        return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}
    return {
        "describe": obj({"action": {"type": "string", "enum": sorted(ACTIONS)}}, []),
        "search": obj({"query": {"type": "string", "maxLength": 200}, "limit": {"type": "integer", "minimum": 1, "maximum": 20}, "cursor": identifier}, []),
        "read": obj({"tool_id": identifier, "revision": revision}, ["tool_id"]),
        "register": obj({"source_path": {"type": "string", "maxLength": 2048},
            "expected_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "manifest": manifest_schema(), "tool_id": identifier, "expected_revision": revision},
            ["source_path", "expected_sha256", "manifest"]),
        "test": obj({"tool_id": identifier, "revision": revision}, ["tool_id", "revision"]),
        "run": obj({"tool_id": identifier, "revision": revision, "arguments": {"type": "object"}},
                    ["tool_id", "revision", "arguments"]),
    }


def registration_example() -> Json:
    source = "import json, sys\nargs = json.load(sys.stdin)\nprint(json.dumps(args['value'] + 1))\n"
    return {"source_file": "add_one.py", "source": source,
        "instruction": "Write this exact UTF-8 source through file tools. For modified source, read its fresh SHA-256.",
        "call": {"action": "register", "payload": {
            "source_path": "add_one.py", "expected_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "manifest": {"name": "add_one", "description": "Add one to an integer.",
                "input_schema": {"type": "object", "properties": {"value": {"type": "integer"}},
                    "required": ["value"], "additionalProperties": False},
                "output_schema": {"type": "integer"},
                "tests": [{"name": "one becomes two", "arguments": {"value": 1}, "expected": 2}]}}}}


def validate_action(action: Any, payload: Any) -> tuple[str, Json]:
    if not isinstance(action, str) or action not in ACTIONS:
        raise KiraError("INVALID_ARGUMENT", "Unknown project tool action")
    schema = action_schemas()[action]
    fields(payload, set(schema["properties"]), set(schema["required"]))
    bounded_json(payload, 60 * 1024)
    for key in ("tool_id", "cursor"):
        if key in payload:
            text(payload[key], key)
    for key in ("revision", "expected_revision"):
        if key in payload:
            integer(payload[key], key)
    if action == "describe" and (not isinstance(payload.get("action", "describe"), str)
                                 or payload.get("action", "describe") not in ACTIONS):
        raise KiraError("INVALID_ARGUMENT", "Unknown described action")
    if action == "register":
        if ("tool_id" in payload) != ("expected_revision" in payload):
            raise KiraError("INVALID_ARGUMENT", "Updating a tool requires its id and expected revision")
        payload = {**payload, "source_path": source_path(payload["source_path"]),
                   "manifest": validate_manifest(payload["manifest"])}
        if not isinstance(payload["expected_sha256"], str) or not SHA256.fullmatch(payload["expected_sha256"]):
            raise KiraError("INVALID_ARGUMENT", "Expected source SHA-256 is required")
    if action == "run" and not isinstance(payload["arguments"], dict):
        raise KiraError("INVALID_ARGUMENT", "Tool arguments must be an object")
    return action, payload


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def project_tool_specs() -> list[Json]:
    return [{"type": "function", "name": "project_tools", "description":
        "Discover, register, test and run this project's Python tools. Use describe for exact contracts. "
        "Test/run requires Execute with trusted_host or full_access; tests cover declared cases only.",
        "inputSchema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": sorted(ACTIONS)}, "payload": {"type": "object"}},
            "required": ["action", "payload"], "additionalProperties": False}}]
