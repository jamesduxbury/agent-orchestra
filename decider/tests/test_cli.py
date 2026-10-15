import io
import json
import subprocess
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


@pytest.fixture
def repo(workspace: Path) -> Path:
    repo = workspace / "repo"
    repo.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "app.py").write_text("one\n")
    subprocess.run([*git, "add", "."], cwd=repo, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "base"], cwd=repo, check=True)
    (repo / "auth").mkdir()
    (repo / "auth" / "token.py").write_text("secret\n")
    return repo


def test_signals_prints_the_diff_summary(workspace, repo, capsys):
    code = main(["signals", "--git-diff", "main", "--repo", str(repo), "--config", str(workspace / "orchestra.toml")])

    assert code == 0
    assert capsys.readouterr().out == (
        "Diff signals (against main):\n"
        "- 1 files changed, +1 -0 lines\n"
        "- Tests touched: no\n"
        "- Sensitive paths: auth/token.py\n"
    )


def test_signals_reports_git_errors_with_exit_two(workspace, repo, capsys):
    code = main(["signals", "--git-diff", "nope", "--repo", str(repo), "--config", str(workspace / "orchestra.toml")])

    assert code == 2
    assert "nope" in capsys.readouterr().err


def test_decide_with_git_diff_still_decides(workspace, repo, capsys):
    _, payload = run(capsys, *decide_args(workspace), "--git-diff", "main", "--repo", str(repo))

    assert payload["status"] == "decided"


def test_decide_with_bad_git_base_abstains_as_input_error(workspace, repo, capsys):
    code, payload = run(capsys, *decide_args(workspace), "--git-diff", "nope", "--repo", str(repo))

    assert code == 0
    assert payload["reason"].startswith("input_error")


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["decide", "tier"], id="missing state file"),
        pytest.param(["decide", "tier", "--state-file", "x", "--no-such-flag"], id="unknown flag"),
        pytest.param(["decide"], id="missing decision"),
    ],
)
def test_decide_argument_errors_still_print_abstain_json(capsys, args):
    code = main(args)

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["status"] == "abstain"
    assert payload["reason"].startswith("input_error")


def test_other_commands_keep_usage_errors(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["signals"])

    assert exit_info.value.code == 2
    assert "--git-diff" in capsys.readouterr().err
