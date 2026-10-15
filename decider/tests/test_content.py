"""The shipped rubrics, golden cases and roles must stay valid and consistent with the skill."""

from pathlib import Path

import pytest

from orchestra_decider.replay import cases_from_golden
from orchestra_decider.roles import load_roles
from orchestra_decider.rubrics import RubricKind, load_rubrics

CORE = Path(__file__).resolve().parents[2] / "core"
SKILL = CORE / "skills" / "orchestrate" / "SKILL.md"


@pytest.fixture(scope="module")
def rubrics():
    return load_rubrics(CORE / "rubrics")


@pytest.fixture(scope="module")
def roles():
    return load_roles(CORE / "roles")


def test_ships_the_four_routing_decisions(rubrics):
    assert sorted(rubrics) == ["panel", "scope", "tier", "verdict"]


def test_tier_options_match_the_workflows(rubrics):
    assert list(rubrics["tier"].options) == ["direct", "single", "tdd"]


def test_verdict_options_match_the_review_loop(rubrics):
    assert list(rubrics["verdict"].options) == ["accept", "revise", "escalate", "ask-human"]


def test_every_panel_reviewer_has_a_role(rubrics, roles):
    panel = rubrics["panel"]
    assert panel.kind is RubricKind.PANEL
    assert panel.baseline == "generalist"
    for option in [panel.baseline, *panel.options]:
        assert f"reviewer-{option}" in roles


def test_ships_the_workflow_roles(roles):
    assert {"scoper", "test-author", "implementer"} <= set(roles)


def test_test_author_and_scoper_use_the_strong_tier(roles):
    assert roles["test-author"].tier == "strong"
    assert roles["scoper"].tier == "strong"
    assert roles["implementer"].tier == "fast"


def test_reviewers_and_scoper_cannot_write(roles):
    for name, role in roles.items():
        if name.startswith("reviewer-") or name == "scoper":
            assert "write" not in role.capabilities, name


def test_golden_cases_cover_every_decision_and_option(rubrics):
    cases = cases_from_golden(CORE / "golden", rubrics)
    assert cases

    for decision, rubric in rubrics.items():
        expected = {
            option
            for case in cases
            if case.rubric.decision == decision
            for option in (case.expected if isinstance(case.expected, list) else [case.expected])
        }
        assert expected == set(rubric.options) | ({rubric.baseline} - {None}), decision


def test_option_descriptions_stay_short_enough_for_small_decision_models(rubrics):
    # Laya rejects option descriptions over 48 tokens.
    for rubric in rubrics.values():
        for option, description in rubric.options.items():
            assert len(description.split()) <= 30, f"{rubric.decision}.{option}"
