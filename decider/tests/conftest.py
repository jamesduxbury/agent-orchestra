import pytest


@pytest.fixture(autouse=True)
def isolated_orchestra_home(tmp_path_factory, monkeypatch):
    """Keep CLI runs away from the real ~/.config and ~/.local/state."""
    home = tmp_path_factory.mktemp("orchestra-home")
    monkeypatch.setenv("ORCHESTRA_STATE_DIR", str(home / "state"))
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(home / "absent.toml"))
    return home
