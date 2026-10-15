import subprocess
from pathlib import Path

import pytest

from orchestra_decider.signals import (
    DiffSignals,
    FileChange,
    SignalsError,
    collect_diff_signals,
    parse_numstat,
    summarise,
    with_signals,
)

SENSITIVE = ["**/auth/**", "**/*.tf", "**/migrations/**"]
TESTS = ["**/tests/**", "**/test_*.py", "**/*Test.java"]


def signals(**overrides) -> DiffSignals:
    defaults = dict(
        base="main",
        files_changed=3,
        lines_added=120,
        lines_removed=30,
        tests_touched=True,
        sensitive_paths=("src/auth/login.py",),
    )
    return DiffSignals(**(defaults | overrides))


def test_parse_numstat_reads_paths_and_line_counts():
    assert parse_numstat("10\t2\tsrc/app.py\n0\t5\tREADME.md\n") == [
        FileChange("src/app.py", 10, 2),
        FileChange("README.md", 0, 5),
    ]


def test_parse_numstat_counts_binary_files_with_zero_lines():
    assert parse_numstat("-\t-\tlogo.png\n") == [FileChange("logo.png", 0, 0)]


def test_parse_numstat_ignores_blank_lines():
    assert parse_numstat("\n1\t1\ta.py\n\n") == [FileChange("a.py", 1, 1)]


def test_summarise_totals_files_and_lines():
    result = summarise("main", [FileChange("a.py", 10, 2), FileChange("b.py", 3, 4)], SENSITIVE, TESTS)

    assert (result.base, result.files_changed, result.lines_added, result.lines_removed) == ("main", 2, 13, 6)


@pytest.mark.parametrize("path", ["auth/session.py", "src/auth/session.py", "infra/main.tf", "db/migrations/001.sql"])
def test_summarise_flags_sensitive_paths_at_any_depth(path):
    assert summarise("main", [FileChange(path, 1, 0)], SENSITIVE, TESTS).sensitive_paths == (path,)


def test_summarise_ignores_paths_outside_sensitive_globs():
    assert summarise("main", [FileChange("src/authoring.py", 1, 0)], SENSITIVE, TESTS).sensitive_paths == ()


@pytest.mark.parametrize("path", ["tests/test_app.py", "pkg/tests/helpers.py", "test_app.py", "src/AppTest.java"])
def test_summarise_detects_touched_tests(path):
    assert summarise("main", [FileChange(path, 1, 0)], SENSITIVE, TESTS).tests_touched


def test_summarise_reports_untouched_tests():
    assert not summarise("main", [FileChange("src/app.py", 1, 0)], SENSITIVE, TESTS).tests_touched


def test_describe_renders_a_readable_block():
    assert signals().describe() == (
        "Diff signals (against main):\n"
        "- 3 files changed, +120 -30 lines\n"
        "- Tests touched: yes\n"
        "- Sensitive paths: src/auth/login.py"
    )


def test_describe_says_none_when_no_sensitive_paths():
    text = signals(sensitive_paths=(), tests_touched=False).describe()

    assert "- Tests touched: no" in text
    assert "- Sensitive paths: none" in text


def test_describe_caps_listed_sensitive_paths():
    paths = tuple(f"auth/f{i}.py" for i in range(13))

    assert signals(sensitive_paths=paths).describe().endswith("auth/f9.py and 3 more")


def test_with_signals_appends_the_block_to_the_state():
    assert with_signals("Task: add login", signals()) == f"Task: add login\n\n{signals().describe()}"


def git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "app.py").write_text("one\ntwo\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def test_collects_tracked_and_untracked_changes_against_base(repo: Path):
    (repo / "app.py").write_text("one\nTWO\nthree\n")
    (repo / "auth").mkdir()
    (repo / "auth" / "token.py").write_text("a\nb\nc\nd\n")

    result = collect_diff_signals("main", repo, SENSITIVE, TESTS)

    assert result.files_changed == 2
    assert (result.lines_added, result.lines_removed) == (6, 1)
    assert result.sensitive_paths == ("auth/token.py",)


def test_untracked_files_respect_gitignore(repo: Path):
    (repo / ".gitignore").write_text("build/\n")
    (repo / "build").mkdir()
    (repo / "build" / "out.txt").write_text("x\n")

    result = collect_diff_signals("main", repo, SENSITIVE, TESTS)

    assert result.files_changed == 1


def test_no_changes_gives_empty_signals(repo: Path):
    result = collect_diff_signals("main", repo, SENSITIVE, TESTS)

    assert (result.files_changed, result.lines_added, result.lines_removed) == (0, 0, 0)


def test_unknown_base_is_a_signals_error(repo: Path):
    with pytest.raises(SignalsError, match="no-such-branch"):
        collect_diff_signals("no-such-branch", repo, SENSITIVE, TESTS)


def test_non_repository_is_a_signals_error(tmp_path: Path):
    with pytest.raises(SignalsError):
        collect_diff_signals("main", tmp_path, SENSITIVE, TESTS)
