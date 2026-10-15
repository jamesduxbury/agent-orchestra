import json
from pathlib import Path

import pytest

from orchestra_decider.confidence import confidence, normalise

FIXTURES = Path(__file__).parent / "fixtures" / "systemone"


def test_uniform_distribution_has_zero_confidence():
    assert confidence({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3}) == pytest.approx(0.0)


def test_certain_distribution_has_full_confidence():
    assert confidence({"a": 1.0, "b": 0.0, "c": 0.0}) == pytest.approx(1.0)


def test_binary_confidence_is_zero_at_even_odds():
    assert confidence({"true": 0.5, "false": 0.5}) == pytest.approx(0.0)


def test_binary_confidence_rises_towards_certainty():
    assert confidence({"true": 0.9, "false": 0.1}) > confidence({"true": 0.7, "false": 0.3})


@pytest.mark.parametrize("fixture", ["mixed_strands-decider.json", "mixed_laya.json"])
def test_matches_confidence_reported_by_systemone(fixture):
    answer = json.loads((FIXTURES / fixture).read_text())["response"]["answers"]["tier"]

    assert confidence(answer["probabilities"]) == pytest.approx(answer["confidence"], abs=1e-6)


def test_normalise_rescales_to_sum_to_one():
    assert normalise({"a": 2.0, "b": 6.0}) == pytest.approx({"a": 0.25, "b": 0.75})


def test_confidence_normalises_before_scoring():
    assert confidence({"a": 2.0, "b": 2.0}) == pytest.approx(0.0)


def test_normalise_rejects_all_zero_distribution():
    with pytest.raises(ValueError):
        normalise({"a": 0.0, "b": 0.0})
