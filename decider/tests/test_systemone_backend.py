import json
from pathlib import Path

import httpx
import pytest

from orchestra_decider.backends import create_backend
from orchestra_decider.backends.systemone_http import SystemOneBackend
from orchestra_decider.config import Config, ConfigError
from orchestra_decider.types import BackendError, DecisionRequest, Question, QuestionKind

FIXTURES = Path(__file__).parent / "fixtures" / "systemone"

TIER = Question(
    "tier",
    QuestionKind.CHOICE,
    "Which workflow weight fits this task?",
    {"direct": "Trivial", "single": "Small", "tdd": "Complex"},
)
SECURITY = Question("panel_security", QuestionKind.BINARY, "Should a security reviewer review this?")
SCOPE = Question(
    "scope",
    QuestionKind.BINARY,
    "Is this under-specified?",
    {"true": "Missing acceptance criteria", "false": "Clear enough"},
)


def request(*questions: Question) -> DecisionRequest:
    return DecisionRequest(state="Add OAuth login", questions=questions)


def fixture_response(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())["response"]


def backend_replying(handler, **kwargs) -> SystemOneBackend:
    defaults = {"url": "http://decider.test", "model": "strands-decider:2b"}
    return SystemOneBackend(**(defaults | kwargs), transport=httpx.MockTransport(handler))


def json_reply(payload: dict, status: int = 200):
    return lambda _request: httpx.Response(status, json=payload)


def test_name_identifies_protocol_and_model():
    assert SystemOneBackend("http://x", "laya").name == "systemone:laya"


def test_request_body_maps_choice_and_binary_questions():
    body = SystemOneBackend("http://x", "laya").request_body(request(TIER, SECURITY, SCOPE))

    assert body == {
        "model": "laya",
        "state": "Add OAuth login",
        "questions": {
            "tier": {
                "type": "choice",
                "instructions": "Which workflow weight fits this task?",
                "criteria": {"direct": "Trivial", "single": "Small", "tdd": "Complex"},
            },
            "panel_security": {
                "type": "noul",
                "instructions": "Should a security reviewer review this?",
            },
            "scope": {
                "type": "noul",
                "instructions": "Is this under-specified?",
                "criteria": {"true": "Missing acceptance criteria", "false": "Clear enough"},
            },
        },
    }


def test_request_body_includes_keep_alive_only_when_configured():
    request_body = SystemOneBackend("http://x", "laya", keep_alive="30m").request_body(request(TIER))

    assert request_body["keep_alive"] == "30m"
    assert "keep_alive" not in SystemOneBackend("http://x", "laya").request_body(request(TIER))


@pytest.mark.parametrize("url", ["http://decider.test", "http://decider.test/"])
def test_posts_the_request_body_to_the_systemone_endpoint(url):
    seen = {}

    def handler(http_request: httpx.Request) -> httpx.Response:
        seen["method"], seen["url"] = http_request.method, str(http_request.url)
        seen["body"] = json.loads(http_request.content)
        return httpx.Response(200, json=fixture_response("mixed_laya.json"))

    backend = backend_replying(handler, url=url)
    backend.decide(request(TIER))

    assert seen["method"] == "POST"
    assert seen["url"] == "http://decider.test/v1/systemone"
    assert seen["body"] == backend.request_body(request(TIER))


@pytest.mark.parametrize("fixture", ["mixed_strands-decider.json", "mixed_laya.json"])
def test_translates_recorded_choice_answers(fixture):
    response = fixture_response(fixture)

    answers = backend_replying(json_reply(response)).decide(request(TIER))

    assert answers["tier"] == response["answers"]["tier"]["probabilities"]


@pytest.mark.parametrize("fixture", ["mixed_strands-decider.json", "mixed_laya.json"])
def test_translates_recorded_noul_answers_to_true_and_false(fixture):
    p_true = fixture_response(fixture)["answers"]["panel_security"]["noul"]

    answers = backend_replying(json_reply(fixture_response(fixture))).decide(request(SECURITY))

    assert answers["panel_security"] == pytest.approx({"true": p_true, "false": 1 - p_true})


def test_ignores_answers_to_questions_it_did_not_ask():
    answers = backend_replying(json_reply(fixture_response("mixed_laya.json"))).decide(request(TIER))

    assert list(answers) == ["tier"]


def test_omits_requested_questions_missing_from_the_response():
    reply = {"model": "laya", "answers": {}}

    assert backend_replying(json_reply(reply)).decide(request(TIER)) == {}


def test_answer_of_the_wrong_type_is_a_backend_error():
    reply = {"answers": {"tier": {"type": "noul", "noul": 0.9}}}

    with pytest.raises(BackendError, match="tier"):
        backend_replying(json_reply(reply)).decide(request(TIER))


def test_server_error_message_is_surfaced():
    reply = json_reply({"error": 'model "nope" not found, try pulling it first'}, status=404)

    with pytest.raises(BackendError, match=r"404.*not found, try pulling it first"):
        backend_replying(reply).decide(request(TIER))


def test_non_json_error_response_reports_status():
    def handler(_request):
        return httpx.Response(502, text="Bad Gateway")

    with pytest.raises(BackendError, match="502"):
        backend_replying(handler).decide(request(TIER))


def test_non_json_success_response_is_a_backend_error():
    def handler(_request):
        return httpx.Response(200, text="<html>")

    with pytest.raises(BackendError, match="JSON"):
        backend_replying(handler).decide(request(TIER))


def test_unreachable_server_is_a_backend_error():
    def handler(http_request):
        raise httpx.ConnectError("connection refused", request=http_request)

    with pytest.raises(BackendError, match="unreachable"):
        backend_replying(handler).decide(request(TIER))


def test_timeout_is_a_backend_error():
    def handler(http_request):
        raise httpx.ReadTimeout("timed out", request=http_request)

    with pytest.raises(BackendError, match="timed out"):
        backend_replying(handler).decide(request(TIER))


def test_from_options_requires_url_and_model():
    with pytest.raises(ConfigError, match="model"):
        SystemOneBackend.from_options({"url": "http://x"})


def test_from_options_reads_optional_settings():
    backend = SystemOneBackend.from_options(
        {"url": "http://x", "model": "laya", "timeout": 2.5, "keep_alive": "1h"}
    )

    assert (backend.timeout, backend.keep_alive) == (2.5, "1h")


def test_default_config_creates_a_systemone_backend():
    config = Config()

    backend = create_backend(config.backend, config.backend_options)

    assert backend.name == "systemone:strands-decider:2b"


@pytest.mark.parametrize(
    ("question", "answer"),
    [
        pytest.param(TIER, {"type": "choice", "choice": "tdd"}, id="choice without probabilities"),
        pytest.param(SECURITY, {"type": "noul"}, id="noul without value"),
    ],
)
def test_answer_missing_its_values_is_a_backend_error(question, answer):
    reply = {"answers": {question.name: answer}}

    with pytest.raises(BackendError, match=question.name):
        backend_replying(json_reply(reply)).decide(request(question))
