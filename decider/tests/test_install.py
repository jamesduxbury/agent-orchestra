import json
import shutil
from pathlib import Path

import pytest

from orchestra_decider.cli import main
from orchestra_decider.config import Config, load_config
from orchestra_decider.install import InstallError, Locations, install
from orchestra_decider.roles import parse_role

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A disposable copy of the shipped content, so tests can add or remove roles."""
    root = tmp_path / "repo"
    shutil.copytree(REPO / "core", root / "core")
    shutil.copytree(REPO / "harnesses", root / "harnesses")
    return root


@pytest.fixture
def locations(tmp_path: Path) -> Locations:
    home = tmp_path / "home"
    return Locations(
        home=home,
        share_dir=home / ".local" / "share" / "orchestra",
        config_path=home / ".config" / "orchestra" / "orchestra.toml",
        codex_home=home / ".codex",
        target=tmp_path / "generic-skills",
    )


def run(harness: str, repo: Path, locations: Locations, **kwargs):
    return install(harness, repo, locations, Config(), **kwargs)


def frontmatter(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text().split("---\n")[1])


# Shared assets and config


def test_installs_shared_rubrics_roles_and_golden_cases(repo, locations):
    run("claude-code", repo, locations)

    share = locations.share_dir
    assert (share / "rubrics" / "tier.yaml").read_text() == (repo / "core" / "rubrics" / "tier.yaml").read_text()
    assert (share / "roles" / "implementer.md").exists()
    assert (share / "golden" / "tier.yaml").exists()


def test_writes_a_config_pointing_at_the_shared_assets(repo, locations):
    run("claude-code", repo, locations)

    config = load_config(locations.config_path)
    assert config.rubrics_dir == locations.share_dir / "rubrics"
    assert config.roles_dir == locations.share_dir / "roles"
    assert config.backend == "systemone"
    assert config.retention_days == 30


def test_keeps_an_existing_config(repo, locations):
    locations.config_path.parent.mkdir(parents=True)
    locations.config_path.write_text('[decider]\nbackend = "static"\n')

    report = run("claude-code", repo, locations)

    assert locations.config_path.read_text() == '[decider]\nbackend = "static"\n'
    assert str(locations.config_path) in report.skipped


# Harness targets


def test_claude_code_gets_the_skill_with_its_dispatch_guide(repo, locations):
    run("claude-code", repo, locations)

    skill = locations.home / ".claude" / "skills" / "orchestrate"
    assert (skill / "SKILL.md").read_text() == (repo / "core" / "skills" / "orchestrate" / "SKILL.md").read_text()
    assert (skill / "dispatch.md").read_text() == (repo / "harnesses" / "claude-code" / "dispatch.md").read_text()


def test_claude_code_agents_map_tier_to_model_and_capabilities_to_tools(repo, locations):
    run("claude-code", repo, locations)

    agents = locations.home / ".claude" / "agents"
    implementer = frontmatter(agents / "orchestra-implementer.md")
    test_author = frontmatter(agents / "orchestra-test-author.md")
    reviewer = frontmatter(agents / "orchestra-reviewer-generalist.md")

    assert implementer["name"] == "orchestra-implementer"
    assert implementer["model"] == "sonnet"
    assert test_author["model"] == "opus"
    assert implementer["tools"] == "Read, Grep, Glob, Edit, Write, Bash"
    assert reviewer["tools"] == "Read, Grep, Glob, Bash"


def test_claude_code_agent_body_is_the_role_instructions(repo, locations):
    run("claude-code", repo, locations)

    role = parse_role((repo / "core" / "roles" / "implementer.md").read_text())
    agent = (locations.home / ".claude" / "agents" / "orchestra-implementer.md").read_text()
    assert agent.endswith(role.body)


def test_codex_gets_the_skill_with_its_dispatch_guide(repo, locations):
    run("codex", repo, locations)

    skill = locations.codex_home / "skills" / "orchestrate"
    assert (skill / "SKILL.md").exists()
    assert (skill / "dispatch.md").read_text() == (repo / "harnesses" / "codex" / "dispatch.md").read_text()


def test_generic_installs_into_the_target_directory(repo, locations):
    run("generic", repo, locations)

    skill = locations.target / "orchestrate"
    assert (skill / "dispatch.md").read_text() == (repo / "harnesses" / "generic" / "dispatch.md").read_text()


def test_generic_requires_a_target(repo, locations):
    with pytest.raises(InstallError, match="target"):
        run("generic", repo, Locations(**{**locations.__dict__, "target": None}))


def test_unknown_harness_is_rejected(repo, locations):
    with pytest.raises(InstallError, match="nope"):
        run("nope", repo, locations)


def test_missing_repository_content_is_reported(tmp_path, locations):
    with pytest.raises(InstallError, match="core"):
        run("claude-code", tmp_path / "empty", locations)


# Idempotence and safety


def test_reinstalling_changes_nothing(repo, locations):
    first = run("claude-code", repo, locations)
    second = run("claude-code", repo, locations)

    assert first.written
    assert second.written == []
    assert sorted(second.unchanged) == sorted(first.written)


def test_updates_files_it_wrote_when_the_source_changes(repo, locations):
    run("claude-code", repo, locations)
    skill_source = repo / "core" / "skills" / "orchestrate" / "SKILL.md"
    skill_source.write_text(skill_source.read_text() + "\nNew guidance.\n")

    report = run("claude-code", repo, locations)

    installed = locations.home / ".claude" / "skills" / "orchestrate" / "SKILL.md"
    assert str(installed) in report.written
    assert installed.read_text().endswith("New guidance.\n")


def test_does_not_overwrite_files_the_user_edited(repo, locations):
    run("claude-code", repo, locations)
    installed = locations.home / ".claude" / "skills" / "orchestrate" / "dispatch.md"
    installed.write_text("my own notes")
    source = repo / "harnesses" / "claude-code" / "dispatch.md"
    source.write_text(source.read_text() + "\nchanged\n")

    report = run("claude-code", repo, locations)

    assert installed.read_text() == "my own notes"
    assert str(installed) in report.skipped


def test_does_not_overwrite_files_it_did_not_write(repo, locations):
    existing = locations.home / ".claude" / "agents" / "orchestra-implementer.md"
    existing.parent.mkdir(parents=True)
    existing.write_text("someone else's agent")

    report = run("claude-code", repo, locations)

    assert existing.read_text() == "someone else's agent"
    assert str(existing) in report.skipped


def test_force_overwrites_edited_files(repo, locations):
    run("claude-code", repo, locations)
    installed = locations.home / ".claude" / "skills" / "orchestrate" / "dispatch.md"
    installed.write_text("my own notes")

    run("claude-code", repo, locations, force=True)

    assert installed.read_text() == (repo / "harnesses" / "claude-code" / "dispatch.md").read_text()


def test_removes_files_for_roles_that_no_longer_exist(repo, locations):
    run("claude-code", repo, locations)
    (repo / "core" / "roles" / "reviewer-performance.md").unlink()

    report = run("claude-code", repo, locations)

    agent = locations.home / ".claude" / "agents" / "orchestra-reviewer-performance.md"
    assert not agent.exists()
    assert str(agent) in report.removed


def test_dry_run_reports_without_writing(repo, locations):
    report = run("claude-code", repo, locations, dry_run=True)

    assert report.written
    assert not locations.home.exists()


def test_harness_installs_do_not_remove_each_others_files(repo, locations):
    run("claude-code", repo, locations)
    run("codex", repo, locations)

    assert (locations.home / ".claude" / "skills" / "orchestrate" / "SKILL.md").exists()
    assert (locations.codex_home / "skills" / "orchestrate" / "SKILL.md").exists()


# CLI


def test_cli_install_prints_the_report(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("ORCHESTRA_CONFIG", str(tmp_path / "home" / ".config" / "orchestra" / "orchestra.toml"))
    monkeypatch.delenv("CODEX_HOME", raising=False)

    code = main(["install", "--harness", "codex", "--home", str(tmp_path / "home")])

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["harness"] == "codex"
    assert str(tmp_path / "home" / ".codex" / "skills" / "orchestrate" / "SKILL.md") in report["written"]


def test_generic_installs_to_different_targets_coexist(repo, locations, tmp_path):
    run("generic", repo, locations)
    other = Locations(**{**locations.__dict__, "target": tmp_path / "other-skills"})

    report = run("generic", repo, other)

    assert report.removed == []
    assert (locations.target / "orchestrate" / "SKILL.md").exists()
    assert (other.target / "orchestrate" / "SKILL.md").exists()


def test_installs_into_different_homes_sharing_assets_coexist(repo, locations, tmp_path):
    run("claude-code", repo, locations)
    second_home = Locations(**{**locations.__dict__, "home": tmp_path / "second-home"})

    run("claude-code", repo, second_home)

    assert (locations.home / ".claude" / "agents" / "orchestra-implementer.md").exists()


def test_relative_locations_are_resolved(repo, locations, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    relative = Locations(**{**locations.__dict__, "target": Path("rel-skills")})

    report = run("generic", repo, relative)

    assert str(tmp_path.resolve() / "rel-skills" / "orchestrate" / "SKILL.md") in report.written


def test_adopts_a_legacy_per_harness_manifest(repo, locations):
    run("claude-code", repo, locations)
    manifests = locations.share_dir / "manifests"
    (current,) = manifests.glob("claude-code-*.json")
    current.rename(manifests / "claude-code.json")
    skill_source = repo / "core" / "skills" / "orchestrate" / "SKILL.md"
    skill_source.write_text(skill_source.read_text() + "\nNew guidance.\n")

    report = run("claude-code", repo, locations)

    assert str(locations.home.resolve() / ".claude" / "skills" / "orchestrate" / "SKILL.md") in report.written
