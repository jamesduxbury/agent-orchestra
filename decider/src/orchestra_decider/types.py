from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Literal


class QuestionKind(StrEnum):
    CHOICE = "choice"
    BINARY = "binary"


@dataclass(frozen=True)
class Question:
    name: str
    kind: QuestionKind
    instructions: str
    # CHOICE: option → description. BINARY: {"true": ..., "false": ...} or None.
    criteria: dict[str, str | None] | None = None


@dataclass(frozen=True)
class DecisionRequest:
    state: str
    questions: tuple[Question, ...]


# Question name → distribution. BINARY answers use the keys "true" and "false".
BackendAnswers = dict[str, dict[str, float]]


class BackendError(Exception):
    pass


@dataclass
class DecisionResult:
    id: str
    decision: str
    status: Literal["decided", "abstain"]
    choice: str | list[str] | None
    suggested: str | list[str] | None
    distribution: dict[str, float]
    confidence: float | None
    backend: str
    latency_ms: int
    reason: str | None = None
    task: str | None = None

    def to_json(self) -> dict:
        return asdict(self)
