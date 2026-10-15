import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from .config import DEFAULT_BACKEND_OPTIONS, Config
from .roles import Role, load_roles

HARNESSES = ("claude-code", "codex", "generic")


class InstallError(Exception):
    pass


@dataclass
class InstallReport:
    harness: str
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "harness": self.harness,
            "written": self.written,
            "unchanged": self.unchanged,
            "skipped": self.skipped,
            "removed": self.removed,
        }


@dataclass(frozen=True)
class Locations:
    home: Path
    share_dir: Path
    config_path: Path
    codex_home: Path
    target: Path | None = None


def install(
    harness: str,
    repo_root: Path,
    locations: Locations,
    config: Config,
    force: bool = False,
    dry_run: bool = False,
) -> InstallReport:
    if harness not in HARNESSES:
        raise InstallError(f"unknown harness {harness!r}, choose from {', '.join(HARNESSES)}")
    core = repo_root / "core"
    if not core.is_dir():
        raise InstallError(f"no core/ directory under {repo_root}. Install the CLI from a checkout with `uv tool install --editable`")

    locations = _resolved(locations)
    report = InstallReport(harness)
    root, files = _harness_files(harness, repo_root, locations, config)
    manifests = locations.share_dir / "manifests"
    # One manifest per install location, so installing elsewhere never removes another install's files.
    harness_manifest = manifests / f"{harness}-{hashlib.sha256(str(root).encode()).hexdigest()[:12]}.json"
    _adopt_legacy_manifest(manifests / f"{harness}.json", harness_manifest, root, dry_run)

    _sync(_shared_files(core, locations), manifests / "shared.json", report, force, dry_run)
    _sync(files, harness_manifest, report, force, dry_run)
    _write_config_once(locations, report, dry_run)
    return report


def _resolved(locations: Locations) -> Locations:
    def resolve(path: Path | None) -> Path | None:
        return path.expanduser().resolve() if path is not None else None

    return Locations(**{name: resolve(value) for name, value in locations.__dict__.items()})


def _adopt_legacy_manifest(legacy: Path, manifest: Path, root: Path, dry_run: bool) -> None:
    """Early installs kept one manifest per harness. Carry over the entries under this install's root."""
    if dry_run or manifest.exists() or not legacy.exists():
        return
    entries = {path: digest for path, digest in _read_manifest(legacy).items() if Path(path).is_relative_to(root)}
    manifest.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n")


def _shared_files(core: Path, locations: Locations) -> dict[Path, str]:
    files = {}
    for folder, pattern in (("rubrics", "*.yaml"), ("roles", "*.md"), ("golden", "*.yaml")):
        for source in sorted((core / folder).glob(pattern)):
            files[locations.share_dir / folder / source.name] = source.read_text()
    return files


def _harness_files(harness: str, repo_root: Path, locations: Locations, config: Config) -> tuple[Path, dict[Path, str]]:
    """The directory this harness install owns, and the files to write under it."""
    skill = (repo_root / "core" / "skills" / "orchestrate" / "SKILL.md").read_text()
    dispatch = (repo_root / "harnesses" / harness / "dispatch.md").read_text()
    dispatch = dispatch.replace("{{strong_model}}", config.harnesses[harness].strong)

    if harness == "claude-code":
        root = locations.home / ".claude"
        skill_dir = root / "skills" / "orchestrate"
        models = config.harnesses["claude-code"]
        agents_dir = root / "agents"
        agents = {
            agents_dir / f"orchestra-{role.name}.md": _claude_agent(role, models.strong if role.tier == "strong" else models.fast)
            for role in load_roles(repo_root / "core" / "roles").values()
        }
    elif harness == "codex":
        root = locations.codex_home
        skill_dir, agents = root / "skills" / "orchestrate", {}
    else:
        if locations.target is None:
            raise InstallError("the generic harness needs --target, the directory your harness loads skills from")
        root = locations.target
        skill_dir, agents = root / "orchestrate", {}

    return root, {skill_dir / "SKILL.md": skill, skill_dir / "dispatch.md": dispatch, **agents}


def _claude_agent(role: Role, model: str) -> str:
    tools = ", ".join(role.tools)
    return (
        "---\n"
        f"name: orchestra-{role.name}\n"
        f"description: {json.dumps(role.description)}\n"
        f"model: {model}\n"
        f"tools: {tools}\n"
        "---\n\n"
        f"{role.body}"
    )


def _sync(files: dict[Path, str], manifest_path: Path, report: InstallReport, force: bool, dry_run: bool) -> None:
    manifest = _read_manifest(manifest_path)
    updated = {}

    for path, content in files.items():
        digest = _digest(content)
        if path.exists():
            current = _digest(path.read_text())
            if current == digest:
                report.unchanged.append(str(path))
                updated[str(path)] = digest
                continue
            if manifest.get(str(path)) != current and not force:
                # Either the user edited a file we wrote, or it was never ours.
                report.skipped.append(str(path))
                if str(path) in manifest:
                    updated[str(path)] = manifest[str(path)]
                continue
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        report.written.append(str(path))
        updated[str(path)] = digest

    for stale, recorded in manifest.items():
        if stale in updated:
            continue
        path = Path(stale)
        if not path.exists():
            continue
        if _digest(path.read_text()) != recorded and not force:
            report.skipped.append(stale)
            updated[stale] = recorded
            continue
        if not dry_run:
            path.unlink()
        report.removed.append(stale)

    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(updated, indent=2, sort_keys=True) + "\n")


def _write_config_once(locations: Locations, report: InstallReport, dry_run: bool) -> None:
    path = locations.config_path
    systemone = DEFAULT_BACKEND_OPTIONS["systemone"]
    content = f"""# Created by `orchestra install`. Edit freely, the installer never overwrites this file.

[decider]
backend = "systemone"
rubrics_dir = {json.dumps(str(locations.share_dir / "rubrics"))}
roles_dir = {json.dumps(str(locations.share_dir / "roles"))}

[backends.systemone]
url = {json.dumps(systemone["url"])}
model = {json.dumps(systemone["model"])}

[log]
retention_days = 30
stale_days = 60
"""
    if path.exists():
        (report.unchanged if path.read_text() == content else report.skipped).append(str(path))
        return
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    report.written.append(str(path))


def _read_manifest(path: Path) -> dict[str, str]:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()
