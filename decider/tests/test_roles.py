from pathlib import Path

import pytest

from orchestra_decider.roles import RoleError, load_roles, parse_role

IMPLEMENTER = """---
name: implementer
description: Makes failing tests pass with the smallest correct change.
tier: fast
capabilities: [read, write, shell]
---

# Implementer

Make the tests pass.
"""


def test_parses_frontmatter_and_body():
    role = parse_role(IMPLEMENTER)

    assert role.name == "implementer"
    assert role.description == "Makes failing tests pass with the smallest correct change."
    assert role.tier == "fast"
    assert role.capabilities == ("read", "write", "shell")
    assert role.body == "# Implementer\n\nMake the tests pass.\n"


def test_prompt_appends_the_brief_to_the_role_instructions():
    prompt = parse_role(IMPLEMENTER).prompt("Make tests/test_login.py pass.")

    assert prompt == "# Implementer\n\nMake the tests pass.\n\n## Brief\n\nMake tests/test_login.py pass.\n"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        pytest.param("# no frontmatter", "frontmatter", id="no frontmatter"),
        pytest.param(IMPLEMENTER.replace("tier: fast", "tier: medium"), "tier", id="unknown tier"),
        pytest.param(IMPLEMENTER.replace("shell]", "deploy]"), "deploy", id="unknown capability"),
        pytest.param(IMPLEMENTER.replace("name: implementer\n", ""), "name", id="missing name"),
        pytest.param(IMPLEMENTER.replace("# Implementer\n\nMake the tests pass.\n", ""), "body", id="empty body"),
    ],
)
def test_rejects_invalid_roles(text: str, message: str):
    with pytest.raises(RoleError, match=message):
        parse_role(text, source="implementer.md")


def test_errors_name_the_source():
    with pytest.raises(RoleError, match="implementer.md"):
        parse_role("# nope", source="implementer.md")


def test_load_roles_keys_by_name(tmp_path: Path):
    (tmp_path / "implementer.md").write_text(IMPLEMENTER)
    (tmp_path / "README.txt").write_text("ignored")

    assert list(load_roles(tmp_path)) == ["implementer"]


def test_role_name_must_match_its_file_name(tmp_path: Path):
    (tmp_path / "coder.md").write_text(IMPLEMENTER)

    with pytest.raises(RoleError, match="coder.md"):
        load_roles(tmp_path)
