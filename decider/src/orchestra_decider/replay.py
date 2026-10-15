from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .backends.base import DecisionBackend
from .decide import decide
from .log import DecisionLog
from .rubrics import Rubric, RubricKind, parse_rubric


class ReplayError(Exception):
    pass


@dataclass(frozen=True)
class Case:
    rubric: Rubric
    state: str
    expected: str | list[str]
    source: str


@dataclass
class Report:
    backend: str
    total: int = 0
    agreed: int = 0
    abstained: int = 0
    by_decision: dict[str, dict[str, int]] = field(default_factory=dict)
    disagreements: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "backend": self.backend,
            "total": self.total,
            "agreed": self.agreed,
            "abstained": self.abstained,
            "accuracy": _accuracy(self.agreed, self.total),
            "by_decision": {
                name: counts | {"accuracy": _accuracy(counts["agreed"], counts["total"])}
                for name, counts in self.by_decision.items()
            },
            "disagreements": self.disagreements,
        }


def cases_from_log(log: DecisionLog) -> list[Case]:
    return [
        Case(parse_rubric(row.rubric), row.state, row.final_choice, row.decision_id)
        for row in log.export()
        if row.final_choice is not None
    ]


def cases_from_golden(directory: Path, rubrics: dict[str, Rubric]) -> list[Case]:
    cases = []
    for path in sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")]):
        try:
            entries = yaml.safe_load(path.read_text()) or []
        except yaml.YAMLError as error:
            raise ReplayError(f"{path.name}: {error}") from error
        for index, entry in enumerate(entries, start=1):
            source = f"{path.name}#{index}"
            cases.append(_golden_case(entry, rubrics, source))
    return cases


def evaluate(cases: Iterable[Case], backend: DecisionBackend) -> Report:
    report = Report(backend=backend.name)
    for case in cases:
        result = decide(case.rubric, case.state, backend)
        agreed = _same(result.suggested, case.expected)
        abstained = result.status == "abstain"

        counts = report.by_decision.setdefault(case.rubric.decision, {"total": 0, "agreed": 0, "abstained": 0})
        counts["total"] += 1
        counts["agreed"] += agreed
        counts["abstained"] += abstained
        report.total += 1
        report.agreed += agreed
        report.abstained += abstained
        if not agreed:
            report.disagreements.append(
                {"source": case.source, "decision": case.rubric.decision, "expected": case.expected, "got": result.suggested}
            )
    return report


def _golden_case(entry: dict, rubrics: dict[str, Rubric], source: str) -> Case:
    if not isinstance(entry, dict) or not isinstance(entry.get("state"), str):
        raise ReplayError(f"{source}: each case needs a 'state' string")
    rubric = rubrics.get(entry.get("decision"))
    if rubric is None:
        raise ReplayError(f"{source}: unknown decision {entry.get('decision')!r}")
    expected = entry.get("expected")
    allowed = rubric.allowed_choices
    valid = (
        isinstance(expected, list) and expected and set(expected) <= allowed
        if rubric.kind is RubricKind.PANEL
        else expected in allowed
    )
    if not valid:
        raise ReplayError(f"{source}: expected {expected!r} is not valid for {rubric.decision}")
    return Case(rubric, entry["state"], expected, source)


def _same(got: str | list[str] | None, expected: str | list[str]) -> bool:
    if isinstance(expected, list):
        return isinstance(got, list) and sorted(got) == sorted(expected)
    return got == expected


def _accuracy(agreed: int, total: int) -> float | None:
    return round(agreed / total, 4) if total else None

