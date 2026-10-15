"""Every backend must honour the same answer contract. Live backends are skipped when unavailable."""

import json
from pathlib import Path

import httpx
import pytest

from orchestra_decider.backends.static import StaticBackend
from orchestra_decider.backends.systemone_http import SystemOneBackend
from orchestra_decider.types import DecisionRequest, Question, QuestionKind

FIXTURES = Path(__file__).parent / "fixtures" / "systemone"
OLLAMA = "http://localhost:11434"

REQUEST = DecisionRequest(
    state="Task: replace session cookie login with OAuth2 PKCE. No acceptance criteria yet.",
    questions=(
        Question("tier", QuestionKind.CHOICE, "Which workflow weight fits this task?",
                 {"direct": "Trivial change", "single": "Small change, one review", "tdd": "Complex, tests first"}),
        Question("panel_security", QuestionKind.BINARY, "Should a security-focused reviewer review this change?"),
    ),
)


def recorded_systemone() -> SystemOneBackend:
    response = json.loads((FIXTURES / "mixed_strands-decider.json").read_text())["response"]
    return SystemOneBackend(
        OLLAMA, "strands-decider:2b", transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=response))
    )


def live_systemone(model: str) -> SystemOneBackend:
    try:
        tags = httpx.get(f"{OLLAMA}/api/tags", timeout=1).json()
    except (httpx.HTTPError, ValueError):
        pytest.skip("Ollama is not running")
    if model not in {m["name"] for m in tags.get("models", [])}:
        pytest.skip(f"{model} is not pulled")
    return SystemOneBackend(OLLAMA, model, timeout=30)


BACKENDS = {
    "static": lambda: StaticBackend(),
    "systemone-recorded": recorded_systemone,
    "systemone-live-strands": lambda: live_systemone("strands-decider:2b"),
    "systemone-live-laya": lambda: live_systemone("laya:latest"),
}


@pytest.fixture(params=list(BACKENDS))
def answers(request):
    return BACKENDS[request.param]().decide(REQUEST)


def test_answers_every_question(answers):
    assert sorted(answers) == ["panel_security", "tier"]


def test_choice_answer_covers_exactly_the_criteria(answers):
    assert set(answers["tier"]) == {"direct", "single", "tdd"}


def test_binary_answer_has_true_and_false(answers):
    assert set(answers["panel_security"]) == {"true", "false"}


def test_answers_are_probability_distributions(answers):
    for distribution in answers.values():
        assert all(0 <= p <= 1 for p in distribution.values())
        assert sum(distribution.values()) == pytest.approx(1, abs=1e-3)
