import itertools

import pytest

from orchestra_decider.backends.static import StaticBackend
from orchestra_decider.decide import decide
from orchestra_decider.rubrics import parse_rubric
from orchestra_decider.types import BackendAnswers, DecisionRequest

TIER_DATA = (
    {
        "decision": "tier",
        "kind": "choice",
        "instructions": "Which workflow weight fits this task?",
        "threshold": 0.3,
        "options": {"direct": "Trivial", "single": "Small", "tdd": "Complex"},
    }
)

PANEL_DATA = (
    {
        "decision": "panel",
        "kind": "panel",
        "instructions": "Review for {option} ({description})?",
        "threshold": 0.2,
        "select": 0.5,
        "baseline": "generalist",
        "options": {"security": "auth", "performance": "speed"},
    }
)


def fake_clock(*readings: float):
    ticks = itertools.chain(readings, itertools.repeat(readings[-1]))
    return lambda: next(ticks)


@pytest.fixture
def tier():
    return parse_rubric(TIER_DATA)


@pytest.fixture
def panel():
    return parse_rubric(PANEL_DATA)


class MalformedBackend:
    name = "malformed"

    def decide(self, request: DecisionRequest) -> BackendAnswers:
        return {}


class RecordingBackend(StaticBackend):
    def decide(self, request: DecisionRequest) -> BackendAnswers:
        self.received = request
        return super().decide(request)


def test_confident_choice_is_decided(tier):
    backend = StaticBackend({"tier": {"direct": 0.02, "single": 0.03, "tdd": 0.95}})

    result = decide(tier, "Rewrite auth", backend)

    assert result.status == "decided"
    assert result.choice == "tdd"
    assert result.suggested == "tdd"
    assert result.distribution == {"direct": 0.02, "single": 0.03, "tdd": 0.95}
    assert result.confidence > tier.threshold
    assert result.reason is None


def test_low_confidence_choice_abstains_but_keeps_the_suggestion(tier):
    backend = StaticBackend({"tier": {"direct": 0.3, "single": 0.4, "tdd": 0.3}})

    result = decide(tier, "Something vague", backend)

    assert result.status == "abstain"
    assert result.choice is None
    assert result.suggested == "single"
    assert result.reason == "low_confidence"


def test_sends_state_and_rubric_questions_to_the_backend(tier):
    backend = RecordingBackend()

    decide(tier, "Rewrite auth", backend)

    assert backend.received.state == "Rewrite auth"
    assert backend.received.questions == tier.questions()


def test_backend_error_abstains_with_reason(tier):
    result = decide(tier, "Rewrite auth", StaticBackend(error="connection refused"))

    assert result.status == "abstain"
    assert result.choice is None
    assert result.suggested is None
    assert result.distribution == {}
    assert result.reason == "backend_error: connection refused"


def test_missing_answer_abstains_as_malformed(tier):
    result = decide(tier, "Rewrite auth", MalformedBackend())

    assert result.status == "abstain"
    assert result.reason.startswith("malformed_response")


def test_answer_with_unknown_options_abstains_as_malformed(tier):
    backend = StaticBackend({"tier": {"direct": 0.1, "yolo": 0.9}})

    result = decide(tier, "Rewrite auth", backend)

    assert result.status == "abstain"
    assert result.reason.startswith("malformed_response")


def test_panel_selects_options_at_or_above_select_threshold(panel):
    backend = StaticBackend(
        {
            "security": {"true": 0.98, "false": 0.02},
            "performance": {"true": 0.03, "false": 0.97},
        }
    )

    result = decide(panel, "Add OAuth login", backend)

    assert result.status == "decided"
    assert result.choice == ["generalist", "security"]
    assert result.distribution == {"security": 0.98, "performance": 0.03}


def test_panel_always_includes_the_baseline_reviewer(panel):
    backend = StaticBackend(
        {
            "security": {"true": 0.03, "false": 0.97},
            "performance": {"true": 0.01, "false": 0.99},
        }
    )

    result = decide(panel, "Fix a typo", backend)

    assert result.status == "decided"
    assert result.choice == ["generalist"]


def test_panel_abstains_when_any_option_is_too_uncertain(panel):
    backend = StaticBackend(
        {
            "security": {"true": 0.55, "false": 0.45},
            "performance": {"true": 0.03, "false": 0.97},
        }
    )

    result = decide(panel, "Touch session handling", backend)

    assert result.status == "abstain"
    assert result.choice is None
    assert result.suggested == ["generalist", "security"]
    assert result.reason == "low_confidence"


def test_records_backend_name_and_latency(tier):
    backend = StaticBackend({"tier": {"direct": 0.02, "single": 0.03, "tdd": 0.95}})

    result = decide(tier, "Rewrite auth", backend, clock=fake_clock(10.0, 10.1125))

    assert result.backend == "static"
    assert result.latency_ms == 112


def test_each_result_gets_a_fresh_decision_id(tier):
    backend = StaticBackend()

    first, second = decide(tier, "a", backend), decide(tier, "a", backend)

    assert first.id.startswith("d_")
    assert first.id != second.id


def test_to_json_serialises_every_field(tier):
    backend = StaticBackend({"tier": {"direct": 0.02, "single": 0.03, "tdd": 0.95}})

    payload = decide(tier, "Rewrite auth", backend).to_json()

    assert payload["decision"] == "tier"
    assert payload["status"] == "decided"
    assert payload["choice"] == "tdd"
    assert isinstance(payload["confidence"], float)
    assert set(payload) == {
        "id",
        "task",
        "decision",
        "status",
        "choice",
        "suggested",
        "distribution",
        "confidence",
        "backend",
        "latency_ms",
        "reason",
    }


def test_panel_confidence_measures_distance_from_the_select_line():
    rubric = parse_rubric(PANEL_DATA | {"select": 0.8, "threshold": 0.3})
    backend = StaticBackend({"security": {"true": 0.5, "false": 0.5}, "performance": {"true": 0.1, "false": 0.9}})

    result = decide(rubric, "Add a CLI flag", backend)

    # 0.5 is far below the 0.8 select line, so it is a confident "no" rather than a coin toss.
    assert result.status == "decided"
    assert result.choice == ["generalist"]
    assert result.confidence == pytest.approx(0.3 / 0.8)
