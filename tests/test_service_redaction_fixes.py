"""Inline credential assignments with artificial values; batch and stream redaction agree."""

import json
import random

import pytest

from kira.service import redaction
from kira.service.redaction import Redactor, StreamRedactor

SECRET_FORMS = [
    ("TOKEN=abcdefghijklmnop", "TOKEN=[REDACTED]"),
    ("export GITHUB_TOKEN=ghp_fixture0123456789abcdef", "export GITHUB_TOKEN=[REDACTED]"),
    ("SECRET_KEY=fixture-insecure-value", "SECRET_KEY=[REDACTED]"),
    ('OPENAI_API_KEY="sk-fixture"', 'OPENAI_API_KEY="[REDACTED]"'),
    ("{'password': 'fixture'}", "{'password': '[REDACTED]'}"),
    ('{"password": "fixture-value", "user": "visible"}',
     '{"password": "[REDACTED]", "user": "visible"}'),
    ("AWS_SECRET_ACCESS_KEY=fixture1234value x", "AWS_SECRET_ACCESS_KEY=[REDACTED] x"),
    ("client_secret: fixture-client-value", "client_secret: [REDACTED]"),
    ("Set-Cookie: session=fixturevalue; Path=/", "Set-Cookie: [REDACTED]; Path=/"),
    ("x-api-key: fixture-header-value", "x-api-key: [REDACTED]"),
    ("db.password = 'short'", "db.password = '[REDACTED]'"),
    ("mysql_passwd: fixture-db-value", "mysql_passwd: [REDACTED]"),
    ("Authorization: Bearer fixture-bearer-value; next", "Authorization: [REDACTED]; next"),
]
PROSE = [
    "tokens: 5",
    "max_tokens: 4096 and input_tokens=123456789",
    "token: 5",
    "Token: a unit of text",
    "password: at least 12 characters",
    'if password == "fixture":',
    "token := compute()",
    "token_url=https://example.invalid/token",
    "Authorization: Bearer short; visible",
    'password: ""',
    '{"token": 123456789, "secret": null}',
]


def streamed(redactor, source, width):
    stream = StreamRedactor(redactor)
    pieces = [stream.feed(source[index:index + width]) for index in range(0, len(source), width)]
    pieces.append(stream.finish())
    assert stream.pending_chars == 0
    return "".join(pieces)


@pytest.mark.parametrize("source,expected", SECRET_FORMS)
@pytest.mark.parametrize("width", [1, 3, 1000])
def test_credential_assignments_are_redacted_in_text_and_streams(source, expected, width):
    redactor = Redactor(values=())
    assert redactor.text(source) == expected
    assert streamed(redactor, source, width) == expected


@pytest.mark.parametrize("source", PROSE)
@pytest.mark.parametrize("width", [1, 1000])
def test_ordinary_prose_code_and_json_literals_stay_readable(source, width):
    redactor = Redactor(values=())
    assert redactor.text(source) == source
    assert streamed(redactor, source, width) == source


@pytest.mark.parametrize("content", [
    'password=fixture-value"tail', 'saved password=[REDACTED]" before', "password='open quote",
    'TOKEN=fixture-value\\"quoted', "token=fixture\\\\", 'note token: "visible" end',
])
def test_redacting_json_encoded_text_keeps_it_valid(content):
    redactor = Redactor(values=())
    encoded = json.dumps({"content": content, "token": 123456789, "password": "fixture-json"})
    decoded = json.loads(redactor.text(encoded))
    assert decoded["token"] == 123456789 and decoded["password"] == "[REDACTED]"
    assert "fixture-value" not in decoded["content"]


def test_bearer_scheme_hold_is_bounded():
    stream = StreamRedactor(Redactor(values=()))
    output = stream.feed("Authorization: Bearer")
    assert output == "Authorization: "
    output += stream.feed(" " * 10_000)
    assert stream.pending_chars <= 512
    output += stream.feed("x") + stream.finish()
    assert output == "Authorization: Bearer" + " " * 10_000 + "x"


def test_anchored_scan_matches_the_full_pattern():
    pieces = ["token", "tokens", "password", "passwd", "secret_key", "api-key", "TOKEN", "Bearer",
              "GITHUB_TOKEN=fixture-value", '"password":', "'secret'=", "max_tokens: 4096", "=", ":",
              '"', "'", "\\", '\\"', " ", "\t", "\n", ",", ";", "x", "abcdefgh", "abc", "token=token=fixture-x"]
    generator = random.Random(18)
    for _ in range(4000):
        text = generator.choice(["", " "]).join(generator.choice(pieces) for _ in range(generator.randint(1, 16)))
        assert redaction._redact_inline(text) == redaction._INLINE.sub(redaction._inline_replacement, text), text
