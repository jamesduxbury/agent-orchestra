import json
import sys
from pathlib import Path

import pytest

from orchestra_decider.cli import main

IMPLEMENTER = """---
name: implementer
description: Makes failing tests pass.
tier: fast
capabilities: [read, write, shell]
---

# Implementer

Make the tests pass.
"""
REVIEWER = """---
name: reviewer-generalist
description: Reviews correctness.
tier: fast
capabilities: [read, shell]
---

# Generalist reviewer

Review it.
"""
# Echoes its arguments and stdin so tests can see exactly what was dispatched.
ECHO = [sys.executable, "-c", "import json, sys; print(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}))"]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    roles = tmp_path / "roles"
    roles.mkdir()
    (roles / "implementer.md").write_text(IMPLEMENTER)
    (roles / "reviewer-generalist.md").write_text(REVIEWER)
    rubrics = tmp_path / "rubrics"
    rubrics.mkdir()
    (rubrics / "tier.yaml").write_text("decision: tier\nkind: choice\ninstructions: Which?\nthreshold: 0.3\noptions:\n  a: A\n  b: B\n")
    (tmp_path / "brief.md").write_text("Make tests/test_login.py pass.")
    echo = json.dumps([*ECHO, "{model}", "{sandbox}"])
    (tmp_path / "orchestra.toml").write_text(
        f'[decider]\nroles_dir = "roles"\nrubrics_dir = "rubrics"\n\n'
        f'[harnesses.fake]\nstrong = "big-model"\nfast = "small-model"\ncommand = {echo}\n'
    )
    return tmp_path


def cli(capsys, workspace: Path, *args: str) -> tuple[int, str, str]:
    code = main([*args, "--config", str(workspace / "orchestra.toml")])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_role_list_prints_names_tiers_and_descriptions(workspace, capsys):
    code, out, _ = cli(capsys, workspace, "role", "list")

    assert code == 0
    assert json.loads(out) == [
        {"name": "implementer", "tier": "fast", "description": "Makes failing tests pass."},
        {"name": "reviewer-generalist", "tier": "fast", "description": "Reviews correctness."},
    ]


def test_role_show_prints_the_prompt_with_the_brief(workspace, capsys):
    code, out, _ = cli(capsys, workspace, "role", "show", "implementer", "--brief-file", str(workspace / "brief.md"))

    assert code == 0
    assert out == "# Implementer\n\nMake the tests pass.\n\n## Brief\n\nMake tests/test_login.py pass.\n"


def test_role_run_pipes_the_prompt_to_the_harness_command(workspace, capsys):
    code, out, _ = cli(
        capsys, workspace, "role", "run", "implementer", "--brief-file", str(workspace / "brief.md"), "--harness", "fake"
    )

    dispatched = json.loads(out)
    assert code == 0
    assert dispatched["argv"] == ["small-model", "workspace-write"]
    assert dispatched["stdin"].endswith("## Brief\n\nMake tests/test_login.py pass.\n")


def test_role_run_uses_a_read_only_sandbox_for_roles_that_cannot_write(workspace, capsys):
    _, out, _ = cli(
        capsys, workspace, "role", "run", "reviewer-generalist", "--brief-file", str(workspace / "brief.md"), "--harness", "fake"
    )

    assert json.loads(out)["argv"] == ["small-model", "read-only"]


def test_role_run_tier_override_escalates_to_the_strong_model(workspace, capsys):
    _, out, _ = cli(
        capsys, workspace, "role", "run", "implementer", "--brief-file", str(workspace / "brief.md"),
        "--harness", "fake", "--tier", "strong",
    )

    assert json.loads(out)["argv"][0] == "big-model"


def test_role_run_dry_run_prints_the_command_without_running_it(workspace, capsys):
    code, out, _ = cli(
        capsys, workspace, "role", "run", "implementer", "--brief-file", str(workspace / "brief.md"),
        "--harness", "fake", "--dry-run",
    )

    assert code == 0
    assert json.loads(out)["command"] == [*ECHO, "small-model", "workspace-write"]


def test_role_run_returns_the_command_exit_code(workspace, capsys):
    (workspace / "orchestra.toml").write_text(
        '[decider]\nroles_dir = "roles"\n\n[harnesses.fake]\nstrong = "a"\nfast = "b"\n'
        f'command = {json.dumps([sys.executable, "-c", "import sys; sys.exit(3)"])}\n'
    )

    code, _, _ = cli(capsys, workspace, "role", "run", "implementer", "--brief-file", str(workspace / "brief.md"), "--harness", "fake")

    assert code == 3


@pytest.mark.parametrize(
    ("args", "message"),
    [
        pytest.param(["role", "show", "nobody"], "nobody", id="unknown role"),
        pytest.param(["role", "run", "implementer", "--brief-file", "BRIEF", "--harness", "nope"], "nope", id="unknown harness"),
        pytest.param(["role", "run", "implementer", "--brief-file", "BRIEF", "--harness", "claude-code"], "command", id="harness without command"),
    ],
)
def test_role_errors_exit_two(workspace, capsys, args, message):
    args = [str(workspace / "brief.md") if a == "BRIEF" else a for a in args]

    code, _, err = cli(capsys, workspace, *args)

    assert code == 2
    assert message in err


def test_rubric_show_prints_the_rubric_yaml(workspace, capsys):
    code, out, _ = cli(capsys, workspace, "rubric", "show", "tier")

    assert code == 0
    assert out == (workspace / "rubrics" / "tier.yaml").read_text()


def test_rubric_show_unknown_decision_exits_two(workspace, capsys):
    code, _, err = cli(capsys, workspace, "rubric", "show", "nope")

    assert code == 2
    assert "nope" in err
