from pathlib import Path

import pytest

from orchestra_decider.config import ConfigError, load_config


def test_missing_config_defaults_to_local_strands_decider(tmp_path: Path):
    config = load_config(tmp_path / "absent.toml")

    assert config.backend == "systemone"
    assert config.backend_options == {
        "url": "http://localhost:11434",
        "model": "strands-decider:2b",
    }
    assert config.rubrics_dir is None


def test_no_path_uses_defaults():
    assert load_config(None).backend == "systemone"


def test_file_selects_backend_and_its_options(tmp_path: Path):
    path = tmp_path / "orchestra.toml"
    path.write_text(
        """
[decider]
backend = "systemone"
rubrics_dir = "rubrics"

[backends.systemone]
url = "http://localhost:8080"
model = "laya"
"""
    )

    config = load_config(path)

    assert config.backend_options == {"url": "http://localhost:8080", "model": "laya"}
    assert config.rubrics_dir == tmp_path / "rubrics"


def test_options_belong_to_the_selected_backend_only(tmp_path: Path):
    path = tmp_path / "orchestra.toml"
    path.write_text(
        """
[decider]
backend = "static"

[backends.systemone]
model = "laya"

[backends.static]
error = "offline"
"""
    )

    config = load_config(path)

    assert config.backend == "static"
    assert config.backend_options == {"error": "offline"}


def test_invalid_toml_is_a_config_error(tmp_path: Path):
    path = tmp_path / "orchestra.toml"
    path.write_text("[decider\n")

    with pytest.raises(ConfigError, match="orchestra.toml"):
        load_config(path)


def test_default_signal_globs_cover_common_sensitive_and_test_paths(tmp_path: Path):
    config = load_config(tmp_path / "absent.toml")

    assert "**/auth/**" in config.sensitive_paths
    assert "**/tests/**" in config.test_paths


def test_signal_globs_can_be_overridden(tmp_path: Path):
    path = tmp_path / "orchestra.toml"
    path.write_text('[signals]\nsensitive_paths = ["**/billing/**"]\ntest_paths = ["**/spec/**"]\n')

    config = load_config(path)

    assert config.sensitive_paths == ("**/billing/**",)
    assert config.test_paths == ("**/spec/**",)


def test_log_retention_defaults(tmp_path: Path):
    config = load_config(tmp_path / "absent.toml")

    assert (config.log_dir, config.retention_days, config.stale_days) == (None, 30, 60)


def test_log_section_overrides_retention_and_directory(tmp_path: Path):
    path = tmp_path / "orchestra.toml"
    path.write_text('[log]\ndir = "state"\nretention_days = 7\nstale_days = 14\n')

    config = load_config(path)

    assert (config.log_dir, config.retention_days, config.stale_days) == (tmp_path / "state", 7, 14)


@pytest.mark.parametrize("section", ["retention_days = -1", 'stale_days = "soon"'])
def test_invalid_retention_is_a_config_error(tmp_path: Path, section: str):
    path = tmp_path / "orchestra.toml"
    path.write_text(f"[log]\n{section}\n")

    with pytest.raises(ConfigError):
        load_config(path)
