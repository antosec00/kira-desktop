"""Positive sanitation contracts using exclusively artificial registered values."""

import pytest

from kira.service.redaction import Redactor, StreamRedactor

CANARY = "fixture-known-value-é-123456"


def test_provider_rotation_masks_current_and_previous_set_and_preserves_environment():
    redactor = Redactor(values=("fixture-environment-secret",))
    redactor.set_provider_values(("fixture-access-one", "fixture-refresh-one"))
    stream = StreamRedactor(redactor)
    before = stream.feed("Visible: fixture-access-")
    redactor.set_provider_values(("fixture-access-two", "fixture-refresh-two"))
    assert before + stream.feed("one; fixture-access-two.") + stream.finish() == "Visible: [REDACTED]; [REDACTED]."
    text = '"access_token": "fixture-access-two", "old": "fixture-refresh-one"'
    assert redactor.text(text) == '"access_token": "[REDACTED]", "old": "[REDACTED]"'
    assert redactor.text("fixture-environment-secret") == "[REDACTED]"
    for index in range(50):
        redactor.set_provider_values((f"fixture-access-generation-{index}",))
    assert len(redactor.values) == 3  # Environment + current + preceding set.



def test_credential_owners_keep_their_values_independently():
    redactor = Redactor(values=())
    redactor.set_provider_values(("fixture-web-token-0001",), owner="deepseek_web")
    redactor.set_provider_values(("fixture-openai-access", "fixture-openai-refresh"),
                                 owner="openai_chatgpt")
    redactor.set_provider_values(("fixture-deepseek-token",), owner="deepseek_account")
    redactor.set_provider_values(("fixture-deepseek-token", "fixture-proxy-key-01"),
                                 owner="deepseek_account")
    for _ in range(3):  # Rotations of one owner never drop another owner's values.
        redactor.set_provider_values(("fixture-openai-access-new",), owner="openai_chatgpt")
    text = ("fixture-web-token-0001 fixture-openai-access-new fixture-deepseek-token "
            "fixture-proxy-key-01")
    assert redactor.text(text) == "[REDACTED] [REDACTED] [REDACTED] [REDACTED]"
    redactor.set_provider_values(("", "fixture-web-token-0002"), owner="deepseek_web")
    assert "" not in redactor.values
    assert redactor.text("fixture-web-token-0001") == "[REDACTED]"  # Preceding set.

@pytest.mark.parametrize("width", [1, 2, 3, 5, 11, 100])
def test_stream_redacts_registered_values_independently_of_fragment_width(width):
    stream = StreamRedactor(Redactor(values=(CANARY,)))
    source = f"Public café: {CANARY}; public answer."
    pieces = [stream.feed(source[index:index + width]) for index in range(0, len(source), width)]
    pieces.append(stream.finish())
    assert "".join(pieces) == "Public café: [REDACTED]; public answer."
    assert stream.pending_chars == 0


@pytest.mark.parametrize("split", range(1, len(CANARY)))
def test_every_registered_value_split_preserves_public_surroundings(split):
    stream = StreamRedactor(Redactor(values=(CANARY,)))
    prefix = stream.feed("Public: " + CANARY[:split])
    assert prefix == "Public: "
    suffix = stream.feed(CANARY[split:] + "; done.")
    assert prefix + suffix + stream.finish() == "Public: [REDACTED]; done."


def test_ordinary_output_is_immediate_and_ambiguous_suffix_is_bounded():
    stream = StreamRedactor(Redactor(values=(CANARY,)))
    assert stream.feed("An ordinary sentence.") == "An ordinary sentence."
    assert stream.feed(CANARY[:8]) == ""
    assert stream.pending_chars == 8
    assert stream.finish() == "[REDACTED]"
    assert stream.pending_chars == 0


@pytest.mark.parametrize("key", ["api_key", "api-key", "apikey", "access_token", "password",
                                 "Authorization"])
@pytest.mark.parametrize("bearer", ["", "Bearer "])
def test_inline_assignments_are_sanitized_across_single_character_fragments(key, bearer):
    stream = StreamRedactor(Redactor(values=()))
    source = f"Public {key}: {bearer}fixture-inline-value; visible."
    output = "".join(stream.feed(character) for character in source) + stream.finish()
    assert "fixture-inline-value" not in output
    assert output.startswith(f"Public {key}: [REDACTED]")
    assert output.endswith("; visible.")
    assert stream.pending_chars == 0


def test_long_inline_values_and_whitespace_do_not_accumulate():
    stream = StreamRedactor(Redactor(values=()))
    assert stream.feed("password ") == "password "
    assert stream.feed(" " * 100) == " " * 100
    assert stream.feed("=fixture-value") == "=[REDACTED]"
    for _ in range(30):
        assert stream.feed("more-fixture-value") == ""
        assert stream.pending_chars <= 7
    assert stream.feed("; visible.") == "; visible."


def test_pending_prefix_limit_fails_closed(monkeypatch):
    monkeypatch.setattr("kira.service.redaction.MAX_PENDING_CHARS", 8)
    stream = StreamRedactor(Redactor(values=(CANARY,)))
    with pytest.raises(ValueError, match="redaction memory limit"):
        stream.feed(CANARY[:12])
    assert stream.pending_chars == 0


def test_private_provider_fields_are_removed_recursively():
    redactor = Redactor(values=(CANARY,))
    payload = [{"role": "assistant", "content": CANARY, "thinking": "private fixture",
                "extra": {"reasoning": ["private fixture"],
                          "nested": [{"analysis": "private fixture", "public": "kept"}]}}]
    assert redactor.clean(payload, private=True) == [{"role": "assistant", "content": "[REDACTED]",
                "extra": {"nested": [{"public": "kept"}]}}]
