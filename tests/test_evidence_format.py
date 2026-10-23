"""Readable evidence uses synthetic payloads and preserves recorded literals."""

import json

import pytest

from kira.ui.evidence_format import MAX_DEPTH, MAX_OUTPUT, MAX_SOURCE, readable_payload


def source(value):
    return json.dumps(value, ensure_ascii=False, indent=2)


def test_multiline_context_and_standalone_json_are_readable():
    payload = source({"content": (
        'Context:\n{"environment":{"application":"Kira Desktop",'
        '"capabilities":["read","inspect"]}}\nEnd of context.'
    )})
    original = payload
    assert readable_payload(payload) == (
        '{\n  "content": |\n    Context:\n    {\n'
        '      "environment": {\n        "application": "Kira Desktop",\n'
        '        "capabilities": [\n          "read",\n          "inspect"\n'
        '        ]\n      }\n    }\n    End of context.\n}'
    )
    assert payload == original


def test_literal_backslashes_are_not_decoded_again():
    text = 'First\n' + r'printf "\\n"; literal \n and \u0041; C:\example\file' + '\n'
    shown = readable_payload(source({"content": text}))
    assert '    printf "\\\\n"; literal \\n and \\u0041; C:\\example\\file\n' in shown
    assert shown.endswith('\n    \n}')
    assert "literal \n" not in shown


@pytest.mark.parametrize("ending", ["last line", "last line,"])
def test_object_separators_never_append_punctuation_to_literal_text(ending):
    shown = readable_payload(source({"content": f"first line\n{ending}", "next": True}))
    assert shown == (
        '{\n  "content": |\n    first line\n    ' + ending + '\n  "next": true\n}'
    )


def test_array_separators_are_outside_literal_block_content():
    shown = readable_payload(source(["first\nlast", "next\nactual comma,", {"done": True}]))
    assert shown == (
        '[\n  |\n    first\n    last\n  |\n    next\n    actual comma,\n'
        '  {\n    "done": true\n  }\n]'
    )


def test_numbers_order_and_scalar_values_are_preserved():
    payload = '{"b":1e+300,"a":-0,"exact":1.2345678901234567890,"items":[true,null,4]}'
    shown = readable_payload(payload)
    assert shown.index('"b"') < shown.index('"a"')
    assert '"b": 1e+300' in shown
    assert '"a": -0' in shown
    assert '"exact": 1.2345678901234567890' in shown
    assert '[\n    true,\n    null,\n    4\n  ]' in shown


def test_inline_json_commands_fenced_and_indented_code_remain_literal():
    lines = [
        'prefix {"x":1} suffix',
        'command --json \'{"x":1}\'',
        '```json', '{"fenced":1}', '```',
        '~~~json', '[1,2]', '~~~',
        '    {"indented":1}', '\t{"tabbed":1}',
    ]
    shown = readable_payload(source({"content": '\n'.join(lines)}))
    for line in lines:
        assert '\n    ' + line in shown


def test_embedded_arrays_and_single_line_json_strings():
    shown = readable_payload(source({"content": '[{"a":1},{"b":2}]'}))
    assert '"content": |\n    [\n      {' in shown
    assert '"a": 1' in shown and '"b": 2' in shown


def test_unicode_markup_and_crlf_remain_plain_text():
    text = 'caffè 🚀 <script>alert(1)</script>\r\n<img src="https://example.invalid/a">'
    shown = readable_payload(source({"content": text}))
    assert 'caffè 🚀 <script>alert(1)</script>\r\n' in shown
    assert '<img src="https://example.invalid/a">' in shown
    assert '&lt;' not in shown


@pytest.mark.parametrize('payload', [
    '{"content":"truncated', '{"content":"done"}\n…', 'not JSON',
    '{"duplicate":1,"duplicate":2}', '{"number":NaN}', '{"number":Infinity}',
    'true', '"ordinary text"', '[1,2,]',
])
def test_malformed_ambiguous_or_noncontainer_input_is_unchanged(payload):
    assert readable_payload(payload) == payload


def test_invalid_embedded_json_does_not_change_text():
    text = 'Text\n{"a":1,"a":2}\n{"x":NaN}\n[truncated]'
    shown = readable_payload(source({"content": text}))
    assert '\n    {"a":1,"a":2}\n    {"x":NaN}\n    [truncated]' in shown


def test_deep_and_excessive_node_inputs_are_left_unchanged():
    for payload in ('[' * (MAX_DEPTH + 2) + '0' + ']' * (MAX_DEPTH + 2),
                    '[' * 2000 + '0' + ']' * 2000, source(list(range(5000)))):
        assert readable_payload(payload) == payload


def test_source_and_expanded_output_have_deterministic_bounds():
    oversized = 'x' * (MAX_SOURCE + 1)
    assert readable_payload(oversized) == oversized[:MAX_OUTPUT - 2] + '\n…'
    # Indentation expands a large literal string without large node counts.
    payload = source({"content": 'x\n' * 30_000})
    shown = readable_payload(payload)
    assert len(shown) == MAX_OUTPUT
    assert shown.endswith('\n…')
    assert shown == readable_payload(payload)


def test_embedded_parsing_budget_leaves_remaining_text_literal():
    lines = ['{"value":1}' for _ in range(70)]
    shown = readable_payload(source({"content": '\n'.join(lines)}))
    assert shown.count('"value": 1') == 64
    assert shown.count('{"value":1}') == 6
