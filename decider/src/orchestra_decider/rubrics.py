from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml

from .types import Question, QuestionKind

MAX_CHOICE_OPTIONS = 26
MAX_PANEL_OPTIONS = 64


class RubricKind(StrEnum):
    CHOICE = "choice"
    PANEL = "panel"


class RubricError(Exception):
    pass


@dataclass(frozen=True)
class Rubric:
    decision: str
    kind: RubricKind
    instructions: str
    options: dict[str, str]
    threshold: float
    # PANEL only: minimum P(true) to select an option, and a reviewer that is always included without asking.
    select: float = 0.5
    baseline: str | None = None

    @property
    def allowed_choices(self) -> set[str]:
        return set(self.options) | ({self.baseline} if self.baseline else set())

    def questions(self) -> tuple[Question, ...]:
        if self.kind is RubricKind.CHOICE:
            return (Question(self.decision, QuestionKind.CHOICE, self.instructions, dict(self.options)),)
        return tuple(
            Question(
                option,
                QuestionKind.BINARY,
                self.instructions.replace("{option}", option).replace("{description}", description),
            )
            for option, description in self.options.items()
        )


def parse_rubric(data: dict) -> Rubric:
    if not isinstance(data, dict):
        raise RubricError("rubric must be a mapping")
    try:
        kind = RubricKind(data.get("kind"))
    except ValueError:
        raise RubricError(f"unknown kind {data.get('kind')!r}") from None

    decision = _non_blank(data, "decision")
    options = _options(data.get("options"), kind)
    baseline = data.get("baseline")
    if baseline is not None and kind is not RubricKind.PANEL:
        raise RubricError("'baseline' only applies to panel rubrics")
    if baseline in options:
        raise RubricError(f"baseline {baseline!r} is always included, so it must not also be an option")

    return Rubric(
        decision=decision,
        kind=kind,
        instructions=_non_blank(data, "instructions"),
        options=options,
        threshold=_probability(data, "threshold"),
        select=_probability(data, "select", fallback=0.5),
        baseline=baseline,
    )


def load_rubric(path: Path) -> Rubric:
    try:
        return parse_rubric(yaml.safe_load(path.read_text()))
    except (RubricError, yaml.YAMLError) as error:
        raise RubricError(f"{path}: {error}") from error


def load_rubrics(directory: Path) -> dict[str, Rubric]:
    rubrics: dict[str, Rubric] = {}
    sources: dict[str, Path] = {}
    for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        rubric = load_rubric(path)
        if rubric.decision in rubrics:
            raise RubricError(
                f"decision {rubric.decision!r} defined in both {sources[rubric.decision].name} and {path.name}"
            )
        rubrics[rubric.decision] = rubric
        sources[rubric.decision] = path
    return rubrics


def _non_blank(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RubricError(f"{key!r} must be a non-blank string")
    return value


def _probability(data: dict, key: str, fallback: float | None = None) -> float:
    value = data.get(key, fallback)
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= 1:
        raise RubricError(f"{key!r} must be a number between 0 and 1")
    return float(value)


def _options(options: object, kind: RubricKind) -> dict[str, str]:
    if not isinstance(options, dict):
        raise RubricError("'options' must be a mapping of option to description")
    low, high = (2, MAX_CHOICE_OPTIONS) if kind is RubricKind.CHOICE else (1, MAX_PANEL_OPTIONS)
    if not low <= len(options) <= high:
        raise RubricError(f"{kind} rubrics need {low} to {high} options, got {len(options)}")
    for option, description in options.items():
        if not isinstance(description, str) or not description.strip():
            raise RubricError(f"option {option!r} needs a description")
    return {str(option): description for option, description in options.items()}
