import copy

import pytest

from kira.domain.contracts import KiraError
from kira.domain.questions import validate_answers, validate_request


def request():
    return {"request_id": "provider-1", "blocking": False, "questions": [
        {"id": "theme", "header": "Appearance", "question": "Which theme?",
         "options": [{"label": "Blue", "description": "Blue accents"}], "isOther": True}]}


def test_choices_and_explicit_other_preserve_input():
    normalized = validate_request(request())
    for text in ("Blue", "Antracite e accenti viola ☀"):
        answers = {"theme": {"answers": [text]}}
        assert validate_answers(normalized["questions"], answers) == answers
    assert normalized["blocking"] is False


@pytest.mark.parametrize("answers", [None, {}, {"wrong": {"answers": ["Blue"]}},
    {"theme": {"answers": []}}, {"theme": {"answers": [" "]}},
    {"theme": {"answers": ["Blue", "Green"]}},
    {"theme": {"answers": ["Blue"], "approve": True}},
    {"theme": {"answers": ["x" * 17000]}},
    {"theme": {"answers": ["\ud800"]}}])
def test_invalid_answers_cannot_become_defaults(answers):
    with pytest.raises(KiraError):
        validate_answers(validate_request(request())["questions"], answers)


def test_choice_only_and_free_text_questions():
    value = request()
    value["questions"][0]["isOther"] = False
    questions = validate_request(value)["questions"]
    with pytest.raises(KiraError):
        validate_answers(questions, {"theme": {"answers": ["Green"]}})
    value["questions"][0]["options"] = None
    assert validate_answers(validate_request(value)["questions"],
                            {"theme": {"answers": ["Free text"]}})


@pytest.mark.parametrize("change", [
    {"isSecret": True}, {"isSecret": "false"}, {"id": "two words"},
    {"id": "x" * 129}, {"question": ""}, {"isOther": "true"},
    {"options": [{"label": "Blue"}, {"label": "Blue "}]},
    {"options": [{"label": str(i)} for i in range(7)]},
    {"options": [{"label": "Blue", "permission": "trusted_host"}]},
])
def test_invalid_questions_rejected_before_persistence(change):
    value = request()
    value["questions"][0].update(change)
    with pytest.raises(KiraError):
        validate_request(value)


def test_unique_question_ids_and_request_bounds():
    value = request()
    value["questions"].append(copy.deepcopy(value["questions"][0]))
    with pytest.raises(KiraError):
        validate_request(value)
    for key, bad in (("blocking", 1), ("request_id", "x" * 257), ("questions", [])):
        value = request()
        value[key] = bad
        with pytest.raises(KiraError):
            validate_request(value)
