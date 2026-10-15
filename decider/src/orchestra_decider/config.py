import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BACKEND = "systemone"
DEFAULT_BACKEND_OPTIONS = {
    "systemone": {"url": "http://localhost:11434", "model": "strands-decider:2b"},
}
DEFAULT_SENSITIVE_PATHS = (
    "**/auth/**",
    "**/security/**",
    "**/*secret*",
    "**/*password*",
    "**/*token*",
    "**/*crypto*",
    "**/migrations/**",
    "**/*.sql",
    "**/*.tf",
    "**/Dockerfile",
    "**/.github/workflows/**",
)
DEFAULT_TEST_PATHS = (
    "**/test/**",
    "**/tests/**",
    "**/__tests__/**",
    "**/test_*.py",
    "**/*_test.py",
    "**/*_test.go",
    "**/*Test.java",
    "**/*Test.kt",
    "**/*.test.*",
    "**/*.spec.*",
)


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Harness:
    strong: str
    fast: str
    # Argument template for `orchestra role run`. Placeholders: {model}, {sandbox} (read-only or
    # workspace-write) and {tools} (comma-separated Claude Code tool names). The prompt arrives on stdin.
    command: tuple[str, ...] = ()


DEFAULT_HARNESSES = {
    "claude-code": Harness(strong="opus", fast="sonnet"),
    "codex": Harness(
        strong="gpt-6.1-sol",
        fast="gpt-6-luna",
        command=("codex", "exec", "-m", "{model}", "-s", "{sandbox}", "--skip-git-repo-check", "-"),
    ),
    # --tools limits the session to the role's tools, and --allowedTools pre-approves them for non-interactive use.
    "generic": Harness(
        strong="opus",
        fast="sonnet",
        command=("claude", "-p", "--model", "{model}", "--tools", "{tools}", "--allowedTools", "{tools}"),
    ),
}


@dataclass(frozen=True)
class Config:
    backend: str = DEFAULT_BACKEND
    backend_options: dict = field(default_factory=lambda: dict(DEFAULT_BACKEND_OPTIONS[DEFAULT_BACKEND]))
    rubrics_dir: Path | None = None
    sensitive_paths: tuple[str, ...] = DEFAULT_SENSITIVE_PATHS
    test_paths: tuple[str, ...] = DEFAULT_TEST_PATHS
    log_dir: Path | None = None
    roles_dir: Path | None = None
    harnesses: dict[str, Harness] = field(default_factory=lambda: dict(DEFAULT_HARNESSES))
    retention_days: int = 30
    stale_days: int = 60


def default_config_path() -> Path:
    if override := os.environ.get("ORCHESTRA_CONFIG"):
        return Path(override)
    return Path.home() / ".config" / "orchestra" / "orchestra.toml"


def load_config(path: Path | None) -> Config:
    if path is None or not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError) as error:
        raise ConfigError(f"{path}: {error}") from error

    decider = data.get("decider", {})
    backend = decider.get("backend", DEFAULT_BACKEND)
    options = DEFAULT_BACKEND_OPTIONS.get(backend, {}) | data.get("backends", {}).get(backend, {})
    signals = data.get("signals", {})
    log = data.get("log", {})
    return Config(
        backend=backend,
        backend_options=options,
        rubrics_dir=_relative_to(path, decider.get("rubrics_dir")),
        sensitive_paths=tuple(signals.get("sensitive_paths", DEFAULT_SENSITIVE_PATHS)),
        test_paths=tuple(signals.get("test_paths", DEFAULT_TEST_PATHS)),
        log_dir=_relative_to(path, log.get("dir")),
        retention_days=_days(log, "retention_days", 30, path),
        stale_days=_days(log, "stale_days", 60, path),
        roles_dir=_relative_to(path, decider.get("roles_dir")),
        harnesses=_harnesses(data.get("harnesses", {}), path),
    )


def _harnesses(section: dict, path: Path) -> dict[str, Harness]:
    harnesses = dict(DEFAULT_HARNESSES)
    for name, settings in section.items():
        base = DEFAULT_HARNESSES.get(name)
        strong = settings.get("strong", base.strong if base else None)
        fast = settings.get("fast", base.fast if base else None)
        if not strong or not fast:
            raise ConfigError(f"{path}: [harnesses.{name}] needs 'strong' and 'fast' models")
        command = settings.get("command", base.command if base else ())
        harnesses[name] = Harness(strong=strong, fast=fast, command=tuple(command))
    return harnesses


def _relative_to(config_path: Path, value: str | None) -> Path | None:
    return config_path.parent / Path(value).expanduser() if value else None


def _days(section: dict, key: str, fallback: int, path: Path) -> int:
    value = section.get(key, fallback)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f"{path}: [log] {key} must be a whole number of days, 0 or more")
    return value
