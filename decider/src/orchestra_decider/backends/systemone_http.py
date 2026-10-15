import httpx

from ..config import ConfigError
from ..types import BackendAnswers, BackendError, DecisionRequest, Question, QuestionKind

QUESTION_TYPES = {QuestionKind.CHOICE: "choice", QuestionKind.BINARY: "noul"}


class SystemOneBackend:
    """Decision models served over `/v1/systemone` (Ollama, llama.cpp)."""

    def __init__(
        self,
        url: str,
        model: str,
        timeout: float = 10.0,
        keep_alive: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.url = url
        self.model = model
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.transport = transport

    @property
    def name(self) -> str:
        return f"systemone:{self.model}"

    @classmethod
    def from_options(cls, options: dict) -> "SystemOneBackend":
        for required in ("url", "model"):
            if not options.get(required):
                raise ConfigError(f"systemone backend needs {required!r}")
        return cls(
            url=options["url"],
            model=options["model"],
            timeout=float(options.get("timeout", 10.0)),
            keep_alive=options.get("keep_alive"),
        )

    def request_body(self, request: DecisionRequest) -> dict:
        body = {
            "model": self.model,
            "state": request.state,
            "questions": {question.name: _question(question) for question in request.questions},
        }
        if self.keep_alive is not None:
            body["keep_alive"] = self.keep_alive
        return body

    def decide(self, request: DecisionRequest) -> BackendAnswers:
        payload = self._post(self.request_body(request))
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            raise BackendError(f"{self.name} response has no 'answers'")
        return {
            question.name: _distribution(question, answers[question.name])
            for question in request.questions
            if question.name in answers
        }

    def _post(self, body: dict) -> dict:
        endpoint = f"{self.url.rstrip('/')}/v1/systemone"
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(endpoint, json=body)
        except httpx.TimeoutException as error:
            raise BackendError(f"{endpoint} timed out after {self.timeout}s") from error
        except httpx.HTTPError as error:
            raise BackendError(f"{endpoint} unreachable: {error}") from error

        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.is_error:
            detail = payload.get("error") if isinstance(payload, dict) else response.text[:200]
            raise BackendError(f"{endpoint} returned {response.status_code}: {detail}")
        if not isinstance(payload, dict):
            raise BackendError(f"{endpoint} returned a non-JSON response")
        return payload


def _question(question: Question) -> dict:
    spec = {"type": QUESTION_TYPES[question.kind], "instructions": question.instructions}
    if question.criteria is not None:
        spec["criteria"] = question.criteria
    return spec


def _distribution(question: Question, answer: dict) -> dict[str, float]:
    expected_type = QUESTION_TYPES[question.kind]
    if not isinstance(answer, dict) or answer.get("type") != expected_type:
        raise BackendError(f"answer for {question.name!r} is not a {expected_type!r} answer")
    try:
        if question.kind is QuestionKind.BINARY:
            return {"true": float(answer["noul"]), "false": 1 - float(answer["noul"])}
        return {option: float(p) for option, p in answer["probabilities"].items()}
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise BackendError(f"answer for {question.name!r} is malformed: {error!r}") from error
