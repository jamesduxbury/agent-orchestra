from ..types import BackendAnswers, BackendError, DecisionRequest, QuestionKind


class StaticBackend:
    """Fixed answers, for tests and offline use. Unconfigured questions get a uniform distribution."""

    name = "static"

    def __init__(self, answers: BackendAnswers | None = None, error: str | None = None):
        self.answers = answers or {}
        self.error = error

    @classmethod
    def from_options(cls, options: dict) -> "StaticBackend":
        return cls(answers=options.get("answers"), error=options.get("error"))

    def decide(self, request: DecisionRequest) -> BackendAnswers:
        if self.error:
            raise BackendError(self.error)
        return {question.name: self._answer(question) for question in request.questions}

    def _answer(self, question) -> dict[str, float]:
        if question.name in self.answers:
            return dict(self.answers[question.name])
        if question.kind is QuestionKind.BINARY:
            return {"true": 0.5, "false": 0.5}
        return {option: 1 / len(question.criteria) for option in question.criteria}
