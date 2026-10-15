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
class Config:
    backend: str = DEFAULT_BACKEND
    backend_options: dict = field(default_factory=lambda: dict(DEFAULT_BACKEND_OPTIONS[DEFAULT_BACKEND]))
    rubrics_dir: Path | None = None
    sensitive_paths: tuple[str, ...] = DEFAULT_SENSITIVE_PATHS
    test_paths: tuple[str, ...] = DEFAULT_TEST_PATHS


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
    return Config(
        backend=backend,
        backend_options=options,
        rubrics_dir=_relative_to(path, decider.get("rubrics_dir")),
        sensitive_paths=tuple(signals.get("sensitive_paths", DEFAULT_SENSITIVE_PATHS)),
        test_paths=tuple(signals.get("test_paths", DEFAULT_TEST_PATHS)),
    )


def _relative_to(config_path: Path, value: str | None) -> Path | None:
    return config_path.parent / Path(value).expanduser() if value else None
