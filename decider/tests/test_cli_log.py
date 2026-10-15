import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from orchestra_decider.cli import main
from orchestra_decider.log import DecisionLog

TIER = {
    "decision": "tier",
    "kind": "choice",
    "instructions": "Which workflow weight fits this task?",
    "threshold": 0.3,
    "options": {"direct": "Trivial", "single": "Small", "tdd": "Complex"},
}
PANEL = {
    "decision": "panel",
    "kind": "panel",
    "instructions": "Review for {option}?",
    "threshold": 0.1,
    "baseline": "generalist",
    "options": {"security": "auth"},
}


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    rubrics = tmp_path / "rubrics"
    rubrics.mkdir()
    (rubrics / "tier.yaml").write_text(yaml.safe_dump(TIER))
    (rubrics / "panel.yaml").write_text(yaml.safe_dump(PANEL))
    (tmp_path / "state.md").write_text("Task: rewrite the auth middleware")
    (tmp_path / "orchestra.toml").write_text(
        '[decider]\nbackend = "static"\nrubrics_dir = "rubrics"\n\n'
        "[backends.static.answers.tier]\ndirect = 0.3\nsingle = 0.4\ntdd = 0.3\n"
    )
    return tmp_path


@pytest.fixture
def state_dir() -> Path:
    return Path(os.environ["ORCHESTRA_STATE_DIR"])


def cli(capsys, workspace: Path, *args: str) -> tuple[int, str, str]:
    code = main([*args, "--config", str(workspace / "orchestra.toml")])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def cli_json(capsys, workspace: Path, *args: str) -> dict:
    code, out, _ = cli(capsys, workspace, *args)
    assert code == 0
    return json.loads(out)


def start_task(capsys, workspace: Path) -> str:
    return cli_json(capsys, workspace, "task", "start", "--title", "Rewrite auth")["task"]


def decide_in(capsys, workspace: Path, task: str, decision: str = "tier") -> dict:
    return cli_json(capsys, workspace, "decide", decision, "--state-file", str(workspace / "state.md"), "--task", task)


def test_task_start_prints_a_task_id(workspace, capsys):
    assert start_task(capsys, workspace).startswith("t_")


def test_decide_with_task_logs_the_decision(workspace, capsys, state_dir):
    task = start_task(capsys, workspace)

    payload = decide_in(capsys, workspace, task)

    assert payload["task"] == task
    (row,) = DecisionLog(state_dir).export()
    assert row.decision_id == payload["id"]


def test_decide_with_unknown_task_abstains_as_input_error(workspace, capsys):
    payload = decide_in(capsys, workspace, "t_20261001_deadbeef")

    assert payload["status"] == "abstain"
    assert payload["reason"].startswith("input_error: unknown task")


def test_outcome_records_the_orchestrator_choice(workspace, capsys):
    task = start_task(capsys, workspace)
    decision = decide_in(capsys, workspace, task)

    payload = cli_json(capsys, workspace, "outcome", decision["id"], "--chosen", "tdd")

    assert payload == {"decision_id": decision["id"], "chosen": "tdd", "overridden": True}


def test_outcome_accepts_comma_separated_panel_choices(workspace, capsys):
    task = start_task(capsys, workspace)
    decision = decide_in(capsys, workspace, task, decision="panel")

    payload = cli_json(capsys, workspace, "outcome", decision["id"], "--chosen", "generalist,security")

    assert payload["chosen"] == ["generalist", "security"]


def test_outcome_for_unknown_decision_exits_two(workspace, capsys):
    code, _, err = cli(capsys, workspace, "outcome", "d_000000000000", "--chosen", "tdd")

    assert code == 2
    assert "d_000000000000" in err


def test_task_finish_records_the_result(workspace, capsys):
    task = start_task(capsys, workspace)

    assert cli_json(capsys, workspace, "task", "finish", task, "--result", "accepted") == {
        "task": task,
        "result": "accepted",
    }


def test_task_finish_twice_exits_two(workspace, capsys):
    task = start_task(capsys, workspace)
    cli(capsys, workspace, "task", "finish", task, "--result", "accepted")

    code, _, err = cli(capsys, workspace, "task", "finish", task, "--result", "accepted")

    assert code == 2
    assert "already finished" in err


def old_finished_task(state_dir: Path) -> Path:
    long_ago = datetime.now(UTC) - timedelta(days=90)
    log = DecisionLog(state_dir, clock=lambda: long_ago)
    task = log.start_task()
    log.finish_task(task, "accepted")
    return state_dir / "tasks" / f"{task}.jsonl"


def test_log_prune_dry_run_lists_without_deleting(workspace, capsys, state_dir):
    path = old_finished_task(state_dir)

    payload = cli_json(capsys, workspace, "log", "prune", "--dry-run")

    assert payload == {"dry_run": True, "removed": [str(path)]}
    assert path.exists()


def test_log_prune_deletes_expired_tasks(workspace, capsys, state_dir):
    path = old_finished_task(state_dir)

    cli_json(capsys, workspace, "log", "prune")

    assert not path.exists()


def test_any_command_prunes_opportunistically(workspace, capsys, state_dir):
    path = old_finished_task(state_dir)

    start_task(capsys, workspace)

    assert not path.exists()


def test_log_export_writes_jsonl_rows(workspace, capsys):
    task = start_task(capsys, workspace)
    first = decide_in(capsys, workspace, task)
    second = decide_in(capsys, workspace, task, decision="panel")

    code, out, _ = cli(capsys, workspace, "log", "export")

    rows = [json.loads(line) for line in out.splitlines()]
    assert code == 0
    assert [row["decision_id"] for row in rows] == [first["id"], second["id"]]
    assert {"state", "rubric", "model_choice", "final_choice", "overridden"} <= set(rows[0])


def test_log_export_filters_and_writes_to_a_file(workspace, capsys, tmp_path):
    task = start_task(capsys, workspace)
    decide_in(capsys, workspace, task)
    panel = decide_in(capsys, workspace, task, decision="panel")
    output = tmp_path / "dataset.jsonl"

    cli(capsys, workspace, "log", "export", "--decision", "panel", "--since", "2000-01-01", "--output", str(output))

    assert [json.loads(line)["decision_id"] for line in output.read_text().splitlines()] == [panel["id"]]


def test_replay_against_golden_cases_reports_accuracy(workspace, capsys):
    golden = workspace / "golden"
    golden.mkdir()
    (golden / "tier.yaml").write_text(
        yaml.safe_dump(
            [
                {"decision": "tier", "state": "Something small", "expected": "single"},
                {"decision": "tier", "state": "Rewrite auth", "expected": "tdd"},
            ]
        )
    )

    payload = cli_json(capsys, workspace, "replay", "--golden", str(golden))

    assert (payload["total"], payload["agreed"], payload["accuracy"]) == (2, 1, 0.5)


def test_replay_from_the_log_with_another_backend(workspace, capsys):
    task = start_task(capsys, workspace)
    decision = decide_in(capsys, workspace, task)
    cli(capsys, workspace, "outcome", decision["id"], "--chosen", "tdd")

    payload = cli_json(
        capsys,
        workspace,
        "replay",
        "--backend",
        "static",
        "--set",
        'answers={"tier": {"direct": 0.0, "single": 0.0, "tdd": 1.0}}',
    )

    assert (payload["total"], payload["agreed"]) == (1, 1)


def test_decision_that_could_not_be_logged_reports_no_task(workspace, capsys, state_dir):
    task = start_task(capsys, workspace)
    (state_dir / "tasks" / f"{task}.jsonl").chmod(0o400)

    payload = decide_in(capsys, workspace, task)

    assert payload["status"] == "abstain"
    assert payload["task"] is None


def test_abstain_before_deciding_reports_no_task(workspace, capsys):
    task = start_task(capsys, workspace)

    payload = cli_json(capsys, workspace, "decide", "tier", "--state-file", "/no/such/state.md", "--task", task)

    assert payload["reason"].startswith("input_error")
    assert payload["task"] is None


def test_log_export_makes_an_existing_output_file_private(workspace, capsys, tmp_path):
    output = tmp_path / "dataset.jsonl"
    output.write_text("old")
    output.chmod(0o644)

    cli(capsys, workspace, "log", "export", "--output", str(output))

    assert output.stat().st_mode & 0o777 == 0o600
