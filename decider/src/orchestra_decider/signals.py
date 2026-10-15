import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

MAX_LISTED_PATHS = 10


class SignalsError(Exception):
    pass


@dataclass(frozen=True)
class DiffSignals:
    base: str
    files_changed: int
    lines_added: int
    lines_removed: int
    tests_touched: bool
    sensitive_paths: tuple[str, ...]

    def describe(self) -> str:
        return "\n".join(
            [
                f"Diff signals (against {self.base}):",
                f"- {self.files_changed} files changed, +{self.lines_added} -{self.lines_removed} lines",
                f"- Tests touched: {'yes' if self.tests_touched else 'no'}",
                f"- Sensitive paths: {_listed(self.sensitive_paths)}",
            ]
        )


@dataclass(frozen=True)
class FileChange:
    path: str
    added: int
    removed: int


def summarise(base: str, changes: list[FileChange], sensitive_globs: list[str], test_globs: list[str]) -> DiffSignals:
    return DiffSignals(
        base=base,
        files_changed=len(changes),
        lines_added=sum(change.added for change in changes),
        lines_removed=sum(change.removed for change in changes),
        tests_touched=any(_matches(change.path, test_globs) for change in changes),
        sensitive_paths=tuple(change.path for change in changes if _matches(change.path, sensitive_globs)),
    )


def parse_numstat(numstat: str) -> list[FileChange]:
    changes = []
    for line in numstat.splitlines():
        if not line.strip():
            continue
        added, removed, path = line.split("\t", 2)
        # Binary files report "-" for both counts.
        changes.append(FileChange(path, _count(added), _count(removed)))
    return changes


def collect_diff_signals(
    base: str, repo: Path, sensitive_globs: list[str], test_globs: list[str]
) -> DiffSignals:
    """Working tree (including untracked files) against `base`."""
    _git(repo, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", error=f"unknown base {base!r}")
    tracked = parse_numstat(_git(repo, "diff", "--numstat", "--no-renames", base))
    untracked = [
        FileChange(path, _line_count(repo / path), 0)
        for path in _git(repo, "ls-files", "--others", "--exclude-standard", "-z").split("\0")
        if path
    ]
    return summarise(base, tracked + untracked, sensitive_globs, test_globs)


def with_signals(state: str, signals: DiffSignals) -> str:
    return f"{state.rstrip()}\n\n{signals.describe()}"


def _git(repo: Path, *args: str, error: str | None = None) -> str:
    try:
        completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    except OSError as exc:
        raise SignalsError(f"cannot run git: {exc}") from exc
    if completed.returncode != 0:
        raise SignalsError(error or completed.stderr.strip() or f"git {args[0]} failed")
    return completed.stdout


def _matches(path: str, globs: list[str]) -> bool:
    return any(PurePosixPath(path).full_match(glob) for glob in globs)


def _count(value: str) -> int:
    return 0 if value == "-" else int(value)


def _line_count(path: Path) -> int:
    try:
        content = path.read_bytes()
    except OSError:
        return 0
    if b"\0" in content:
        return 0
    return content.count(b"\n") + (1 if content and not content.endswith(b"\n") else 0)


def _listed(paths: tuple[str, ...]) -> str:
    if not paths:
        return "none"
    listed = ", ".join(paths[:MAX_LISTED_PATHS])
    hidden = len(paths) - MAX_LISTED_PATHS
    return f"{listed} and {hidden} more" if hidden > 0 else listed
