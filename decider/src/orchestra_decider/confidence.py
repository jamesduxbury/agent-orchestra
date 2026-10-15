import math


def normalise(distribution: dict[str, float]) -> dict[str, float]:
    total = sum(distribution.values())
    if total <= 0:
        raise ValueError("distribution has no probability mass")
    return {option: p / total for option, p in distribution.items()}


def confidence(distribution: dict[str, float]) -> float:
    """1 − normalised Shannon entropy, matching the systemone `confidence` field."""
    probabilities = normalise(distribution)
    if len(probabilities) < 2:
        return 1.0
    entropy = -sum(p * math.log(p) for p in probabilities.values() if p > 0)
    return 1 - entropy / math.log(len(probabilities))
