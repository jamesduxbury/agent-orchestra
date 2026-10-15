from pathlib import Path

import pytest
import yaml

from orchestra_decider.backends.static import StaticBackend
from orchestra_decider.decide import decide
from orchestra_decider.log import DecisionLog
from orchestra_decider.replay import Case, ReplayError, cases_from_golden, cases_from_log, evaluate
from orchestra_decider.rubrics import parse_rubric

TIER = parse_rubric(
    {
        "decision": "tier",
        "kind": "choice",
        "instructions": "Which workflow weight?",
        "threshold": 0.3,
        "options": {"direct": "Trivial", "single": "Small", "tdd": "Complex"},
    }
)
PANEL = parse_rubric(
    {
        "decision": "panel",
        "kind": "panel",
        "instructions": "Review for {option}?",
        "threshold": 0.1,
        "baseline": "generalist",
        "options": {"security": "auth"},
    }
)
PICKS_TDD = StaticBackend({"tier": {"direct": 0.02, "single": 0.03, "tdd": 0.95}})
UNSURE_SINGLE = StaticBackend({"tier": {"direct": 0.3, "single": 0.4, "tdd": 0.3}})


def test_evaluate_counts_agreement_with_expected_choices():
    cases = [Case(TIER, "a", "tdd", "c1"), Case(TIER, "b", "direct", "c2")]

    report = evaluate(cases, PICKS_TDD)

    assert (report.total, report.agreed, report.abstained) == (2, 1, 0)
    assert report.by_decision == {"tier": {"total": 2, "agreed": 1, "abstained": 0}}
    assert report.disagreements == [{"source": "c2", "decision": "tier", "expected": "direct", "got": "tdd"}]


def test_evaluate_compares_the_suggestion_even_when_abstaining():
    report = evaluate([Case(TIER, "a", "single", "c1")], UNSURE_SINGLE)

    assert (report.agreed, report.abstained) == (1, 1)


def test_evaluate_compares_panel_selections_ignoring_order():
    backend = StaticBackend({"generalist": {"true": 0.9, "false": 0.1}, "security": {"true": 0.9, "false": 0.1}})

    report = evaluate([Case(PANEL, "a", ["security", "generalist"], "c1")], backend)

    assert report.agreed == 1


def test_report_json_includes_accuracy():
    payload = evaluate([Case(TIER, "a", "tdd", "c1"), Case(TIER, "b", "direct", "c2")], PICKS_TDD).to_json()

    assert payload["backend"] == "static"
    assert payload["accuracy"] == 0.5
    assert payload["by_decision"]["tier"]["accuracy"] == 0.5


def test_empty_report_has_no_accuracy():
    assert evaluate([], PICKS_TDD).to_json()["accuracy"] is None


def test_cases_from_log_use_final_choices_and_the_logged_rubric(tmp_path: Path):
    log = DecisionLog(tmp_path)
    task = log.start_task()
    overridden = decide(TIER, "Rewrite auth", UNSURE_SINGLE)
    log.record_decision(task, TIER, "Rewrite auth", overridden)
    log.record_outcome(overridden.id, "tdd")
    unresolved = decide(TIER, "Vague", UNSURE_SINGLE)
    log.record_decision(task, TIER, "Vague", unresolved)

    (case,) = cases_from_log(log)

    assert case == Case(TIER, "Rewrite auth", "tdd", overridden.id)


def write_golden(directory: Path, cases: list[dict]) -> None:
    directory.mkdir(exist_ok=True)
    (directory / "cases.yaml").write_text(yaml.safe_dump(cases))


def test_cases_from_golden_resolve_rubrics_by_decision(tmp_path: Path):
    write_golden(tmp_path, [{"decision": "tier", "state": "Fix typo", "expected": "direct"}])

    (case,) = cases_from_golden(tmp_path, {"tier": TIER})

    assert case == Case(TIER, "Fix typo", "direct", "cases.yaml#1")


@pytest.mark.parametrize(
    ("case", "message"),
    [
        pytest.param({"decision": "nope", "state": "x", "expected": "direct"}, "nope", id="unknown decision"),
        pytest.param({"decision": "tier", "state": "x", "expected": "yolo"}, "yolo", id="unknown option"),
        pytest.param({"decision": "tier", "expected": "direct"}, "state", id="missing state"),
    ],
)
def test_invalid_golden_cases_are_rejected(tmp_path: Path, case: dict, message: str):
    write_golden(tmp_path, [case])

    with pytest.raises(ReplayError, match=message):
        cases_from_golden(tmp_path, {"tier": TIER})
