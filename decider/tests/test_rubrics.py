from pathlib import Path

import pytest
import yaml

from orchestra_decider.rubrics import (
    RubricError,
    RubricKind,
    load_rubric,
    load_rubrics,
    parse_rubric,
)
from orchestra_decider.types import QuestionKind


def choice_rubric(**overrides) -> dict:
    return {
        "decision": "tier",
        "kind": "choice",
        "instructions": "Which workflow weight fits this task?",
        "threshold": 0.3,
        "options": {
            "direct": "Trivial change",
            "single": "Small change, one review",
            "tdd": "Complex change, tests first",
        },
    } | overrides


def panel_rubric(**overrides) -> dict:
    return {
        "decision": "panel",
        "kind": "panel",
        "instructions": "Should this change be reviewed for {option} ({description})?",
        "threshold": 0.1,
        "select": 0.5,
        "baseline": "generalist",
        "options": {
            "security": "auth, secrets, input handling",
            "performance": "queries and hot paths",
        },
    } | overrides


def test_parses_choice_rubric_preserving_option_order():
    rubric = parse_rubric(choice_rubric())

    assert rubric.decision == "tier"
    assert rubric.kind is RubricKind.CHOICE
    assert list(rubric.options) == ["direct", "single", "tdd"]
    assert rubric.threshold == 0.3


def test_choice_rubric_builds_one_choice_question_named_after_the_decision():
    (question,) = parse_rubric(choice_rubric()).questions()

    assert question.name == "tier"
    assert question.kind is QuestionKind.CHOICE
    assert question.instructions == "Which workflow weight fits this task?"
    assert question.criteria == {
        "direct": "Trivial change",
        "single": "Small change, one review",
        "tdd": "Complex change, tests first",
    }


def test_panel_rubric_builds_one_binary_question_per_option():
    questions = parse_rubric(panel_rubric()).questions()

    assert [(q.name, q.kind) for q in questions] == [
        ("security", QuestionKind.BINARY),
        ("performance", QuestionKind.BINARY),
    ]
    assert questions[0].instructions == (
        "Should this change be reviewed for security (auth, secrets, input handling)?"
    )


def test_panel_select_and_baseline_are_parsed():
    rubric = parse_rubric(panel_rubric(select=0.6))

    assert rubric.select == 0.6
    assert rubric.baseline == "generalist"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(choice_rubric(kind="score"), id="unknown kind"),
        pytest.param(choice_rubric(threshold=1.5), id="threshold above 1"),
        pytest.param(choice_rubric(threshold=-0.1), id="threshold below 0"),
        pytest.param(choice_rubric(options={"only": "one"}), id="single option"),
        pytest.param(
            choice_rubric(options={f"o{i}": "x" for i in range(27)}), id="too many options"
        ),
        pytest.param(choice_rubric(options={"a": "x", "b": ""}), id="blank description"),
        pytest.param(choice_rubric(instructions=""), id="blank instructions"),
        pytest.param({k: v for k, v in choice_rubric().items() if k != "decision"}, id="no name"),
        pytest.param(panel_rubric(baseline="security"), id="baseline also asked as an option"),
        pytest.param(choice_rubric(baseline="generalist"), id="baseline on a choice rubric"),
        pytest.param(panel_rubric(select=2), id="select above 1"),
    ],
)
def test_rejects_invalid_rubrics(data):
    with pytest.raises(RubricError):
        parse_rubric(data)


def test_load_rubric_reads_yaml(tmp_path: Path):
    path = tmp_path / "tier.yaml"
    path.write_text(yaml.safe_dump(choice_rubric(), sort_keys=False))

    assert load_rubric(path).decision == "tier"


def test_load_rubric_names_the_file_in_errors(tmp_path: Path):
    path = tmp_path / "tier.yaml"
    path.write_text(yaml.safe_dump(choice_rubric(threshold=3)))

    with pytest.raises(RubricError, match="tier.yaml"):
        load_rubric(path)


def test_load_rubrics_keys_by_decision(tmp_path: Path):
    (tmp_path / "tier.yaml").write_text(yaml.safe_dump(choice_rubric()))
    (tmp_path / "panel.yaml").write_text(yaml.safe_dump(panel_rubric()))
    (tmp_path / "notes.txt").write_text("ignored")

    assert sorted(load_rubrics(tmp_path)) == ["panel", "tier"]


def test_load_rubrics_rejects_duplicate_decisions(tmp_path: Path):
    (tmp_path / "a.yaml").write_text(yaml.safe_dump(choice_rubric()))
    (tmp_path / "b.yaml").write_text(yaml.safe_dump(choice_rubric()))

    with pytest.raises(RubricError, match="tier"):
        load_rubrics(tmp_path)
