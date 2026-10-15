from typing import Protocol

from ..types import BackendAnswers, DecisionRequest


class DecisionBackend(Protocol):
    name: str

    def decide(self, request: DecisionRequest) -> BackendAnswers: ...
