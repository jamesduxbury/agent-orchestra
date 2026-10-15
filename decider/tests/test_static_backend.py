import pytest

from orchestra_decider.backends import create_backend
from orchestra_decider.backends.static import StaticBackend
from orchestra_decider.config import ConfigError
from orchestra_decider.types import BackendError, DecisionRequest, Question, QuestionKind

TIER = Question("tier", QuestionKind.CHOICE, "Which?", {"direct": "a", "single": "b", "tdd": "c"})
SECURITY = Question("security", QuestionKind.BINARY, "Security?")


def request(*questions: Question) -> DecisionRequest:
    return DecisionRequest(state="Add a login form", questions=questions)


def test_returns_configured_distribution():
    backend = StaticBackend({"tier": {"direct": 0.1, "single": 0.2, "tdd": 0.7}})

    assert backend.decide(request(TIER)) == {"tier": {"direct": 0.1, "single": 0.2, "tdd": 0.7}}


def test_unconfigured_choice_question_is_uniform():
    answers = StaticBackend().decide(request(TIER))

    assert answers["tier"] == pytest.approx({"direct": 1 / 3, "single": 1 / 3, "tdd": 1 / 3})


def test_unconfigured_binary_question_is_even_odds():
    assert StaticBackend().decide(request(SECURITY)) == {"security": {"true": 0.5, "false": 0.5}}


def test_answers_every_question_in_the_request():
    answers = StaticBackend().decide(request(TIER, SECURITY))

    assert sorted(answers) == ["security", "tier"]


def test_configured_error_raises_backend_error():
    with pytest.raises(BackendError, match="offline"):
        StaticBackend(error="offline").decide(request(TIER))


def test_created_from_config_options_by_name():
    backend = create_backend("static", {"answers": {"security": {"true": 0.9, "false": 0.1}}})

    assert backend.name == "static"
    assert backend.decide(request(SECURITY)) == {"security": {"true": 0.9, "false": 0.1}}


def test_unknown_backend_name_is_a_config_error():
    with pytest.raises(ConfigError, match="nope"):
        create_backend("nope", {})
