from dataclasses import dataclass
from pathlib import Path

import yaml

TIERS = ("strong", "fast")
CAPABILITIES = ("read", "write", "shell")


class RoleError(Exception):
    pass


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    tier: str
    capabilities: tuple[str, ...]
    body: str

    def prompt(self, brief: str) -> str:
        return f"{self.body.rstrip()}\n\n## Brief\n\n{brief.strip()}\n"


def parse_role(text: str, source: str = "<role>") -> Role:
    try:
        return _parse(text)
    except RoleError as error:
        raise RoleError(f"{source}: {error}") from error


def load_roles(directory: Path) -> dict[str, Role]:
    roles = {}
    for path in sorted(directory.glob("*.md")):
        role = parse_role(path.read_text(), source=path.name)
        if role.name != path.stem:
            raise RoleError(f"{path.name}: role name {role.name!r} must match the file name")
        roles[role.name] = role
    return roles


def _parse(text: str) -> Role:
    _, opening, rest = text.partition("---\n")
    frontmatter, closing, body = rest.partition("\n---\n")
    if text[: len(opening)] != "---\n" or not closing:
        raise RoleError("missing YAML frontmatter between --- lines")
    try:
        meta = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as error:
        raise RoleError(f"invalid frontmatter: {error}") from error

    for key in ("name", "description"):
        if not isinstance(meta.get(key), str) or not meta[key].strip():
            raise RoleError(f"{key!r} is required")
    if meta.get("tier") not in TIERS:
        raise RoleError(f"tier must be one of {', '.join(TIERS)}, got {meta.get('tier')!r}")
    capabilities = tuple(meta.get("capabilities") or ())
    unknown = [c for c in capabilities if c not in CAPABILITIES]
    if unknown:
        raise RoleError(f"unknown capabilities {unknown}, allowed: {', '.join(CAPABILITIES)}")
    body = body.lstrip("\n")
    if not body.strip():
        raise RoleError("role body is empty")
    return Role(meta["name"], meta["description"].strip(), meta["tier"], capabilities, body)
