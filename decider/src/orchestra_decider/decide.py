import math
import time
import uuid
from collections.abc import Callable

from .backends.base import DecisionBackend
from .confidence import confidence, normalise
from .rubrics import Rubric, RubricKind
from .types import BackendAnswers, BackendError, DecisionRequest, DecisionResult


class MalformedResponse(Exception):
    pass


def new_decision_id() -> str:
    return f"d_{uuid.uuid4().hex[:12]}"


def abstained(decision: str, reason: str, backend: str, latency_ms: int = 0) -> DecisionResult:
    return DecisionResult(
        id=new_decision_id(),
        decision=decision,
        status="abstain",
        choice=None,
        suggested=None,
        distribution={},
        confidence=None,
        backend=backend,
        latency_ms=latency_ms,
        reason=reason,
    )


def decide(
    rubric: Rubric,
    state: str,
    backend: DecisionBackend,
    clock: Callable[[], float] = time.perf_counter,
) -> DecisionResult:
    request = DecisionRequest(state=state, questions=rubric.questions())
    started = clock()
    try:
        answers = backend.decide(request)
    except BackendError as error:
        return abstained(rubric.decision, f"backend_error: {error}", backend.name, _elapsed_ms(started, clock))
    latency_ms = _elapsed_ms(started, clock)

    try:
        interpret = _interpret_choice if rubric.kind is RubricKind.CHOICE else _interpret_panel
        suggested, distribution, certainty = interpret(rubric, answers)
    except MalformedResponse as error:
        return abstained(rubric.decision, f"malformed_response: {error}", backend.name, latency_ms)

    decided = certainty >= rubric.threshold
    return DecisionResult(
        id=new_decision_id(),
        decision=rubric.decision,
        status="decided" if decided else "abstain",
        choice=suggested if decided else None,
        suggested=suggested,
        distribution=distribution,
        confidence=certainty,
        backend=backend.name,
        latency_ms=latency_ms,
        reason=None if decided else "low_confidence",
    )


def _interpret_choice(rubric: Rubric, answers: BackendAnswers) -> tuple[str, dict[str, float], float]:
    distribution = _answer(answers, rubric.decision, list(rubric.options))
    suggested = max(rubric.options, key=lambda option: distribution[option])
    return suggested, distribution, _confidence(distribution, rubric.decision)


def _interpret_panel(rubric: Rubric, answers: BackendAnswers) -> tuple[list[str], dict[str, float], float]:
    probability_true: dict[str, float] = {}
    margins = []
    for option in rubric.options:
        answer = _answer(answers, option, ["true", "false"])
        p_true = _normalised(answer, option)["true"]
        probability_true[option] = p_true
        # How far the option sits from flipping, scaled so 1 means as far from the select line as possible.
        margins.append(abs(p_true - rubric.select) / max(rubric.select, 1 - rubric.select))

    selected = [option for option, p in probability_true.items() if p >= rubric.select]
    if rubric.baseline:
        selected.insert(0, rubric.baseline)
    return selected, probability_true, min(margins)


def _answer(answers: BackendAnswers, question: str, expected: list[str]) -> dict[str, float]:
    if question not in answers:
        raise MalformedResponse(f"no answer for {question!r}")
    answer = answers[question]
    if set(answer) != set(expected):
        raise MalformedResponse(f"answer for {question!r} has options {sorted(answer)}, expected {sorted(expected)}")
    for option, probability in answer.items():
        if not _is_probability(probability):
            raise MalformedResponse(f"answer for {question!r} gives {option!r} the invalid probability {probability!r}")
    # Report the backend's probabilities in rubric order, unaltered, for logging and replay.
    return {option: answer[option] for option in expected}


def _is_probability(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1


def _confidence(distribution: dict[str, float], question: str) -> float:
    return confidence(_normalised(distribution, question))


def _normalised(distribution: dict[str, float], question: str) -> dict[str, float]:
    try:
        return normalise(distribution)
    except ValueError as error:
        raise MalformedResponse(f"answer for {question!r}: {error}") from error


def _elapsed_ms(started: float, clock: Callable[[], float]) -> int:
    return int((clock() - started) * 1000)
