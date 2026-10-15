import io
import json
from pathlib import Path

import pytest
import yaml

from orchestra_decider.cli import main

TIER = {
    "decision": "tier",
    "kind": "choice",
    "instructions": "Which workflow weight fits this task?",
    "threshold": 0.3,
    "options": {"direct": "Trivial", "single": "Small", "tdd": "Complex"},
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    rubrics = tmp_path / "rubrics"
    rubrics.mkdir()
    (rubrics / "tier.yaml").write_text(yaml.safe_dump(TIER))
    (tmp_path / "state.md").write_text("Task: rewrite the auth middleware")
    write_config(tmp_path, '[backends.static.answers.tier]\ndirect = 0.02\nsingle = 0.03\ntdd = 0.95\n')
    return tmp_path


def write_config(root: Path, backend_section: str) -> None:
    (root / "orchestra.toml").write_text(
        f'[decider]\nbackend = "static"\nrubrics_dir = "rubrics"\n\n{backend_section}'
    )


def run(capsys, *args: str) -> tuple[int, dict]:
    code = main(list(args))
    return code, json.loads(capsys.readouterr().out)


def decide_args(root: Path, decision: str = "tier", state: str | None = None) -> list[str]:
    return [
        "decide",
        decision,
        "--state-file",
        state or str(root / "state.md"),
        "--config",
        str(root / "orchestra.toml"),
    ]


def test_decide_prints_decision_json(workspace, capsys):
    code, payload = run(capsys, *decide_args(workspace))

    assert code == 0
    assert payload["status"] == "decided"
    assert payload["choice"] == "tdd"
    assert payload["backend"] == "static"


def test_decide_reads_state_from_stdin(workspace, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("Task from stdin"))

    code, payload = run(capsys, *decide_args(workspace, state="-"))

    assert code == 0
    assert payload["choice"] == "tdd"


def test_rubrics_dir_flag_overrides_config(workspace, capsys, tmp_path_factory):
    other = tmp_path_factory.mktemp("other")
    (other / "tier.yaml").write_text(yaml.safe_dump(TIER | {"threshold": 0.99}))

    _, payload = run(capsys, *decide_args(workspace), "--rubrics-dir", str(other))

    assert payload["status"] == "abstain"


@pytest.mark.parametrize(
    ("args_override", "reason"),
    [
        pytest.param({"decision": "nope"}, "unknown_decision", id="unknown decision"),
        pytest.param({"state": "/no/such/state.md"}, "input_error", id="missing state file"),
    ],
)
def test_input_problems_abstain_with_exit_zero(workspace, capsys, args_override, reason):
    code, payload = run(capsys, *decide_args(workspace, **args_override))

    assert code == 0
    assert payload["status"] == "abstain"
    assert payload["reason"].startswith(reason)


def test_config_problems_abstain_with_exit_zero(workspace, capsys):
    (workspace / "orchestra.toml").write_text('[decider]\nbackend = "nope"\n')

    code, payload = run(capsys, *decide_args(workspace))

    assert code == 0
    assert payload["status"] == "abstain"
    assert payload["reason"].startswith("config_error")


def test_backend_errors_abstain_with_exit_zero(workspace, capsys):
    write_config(workspace, '[backends.static]\nerror = "offline"\n')

    code, payload = run(capsys, *decide_args(workspace))

    assert code == 0
    assert payload["reason"] == "backend_error: offline"
