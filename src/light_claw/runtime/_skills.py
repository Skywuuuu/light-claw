"""Shared utilities for skill discovery across CLI runtimes."""

from __future__ import annotations

from pathlib import Path


def parse_skill_frontmatter(text: str) -> tuple[str, str] | None:
    """Extract name and description from SKILL.md YAML frontmatter.

    Returns (name, description) or None if the file lacks valid frontmatter.
    Handles the simple ``name:`` / ``description:`` pattern without a YAML library.
    """
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    frontmatter = text[3:end]
    name = ""
    description = ""
    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            name = stripped[5:].strip().strip("\"'")
        elif stripped.startswith("description:"):
            description = stripped[12:].strip().strip("\"'")
    if not name:
        return None
    return (name, description[:200] if description else "")


def scan_skills_dir(skills_dir: Path) -> list[tuple[str, str]]:
    """Scan a directory of skill subdirectories for SKILL.md metadata.

    Each child directory that contains a ``SKILL.md`` with valid frontmatter
    contributes one (name, description) entry.  Hidden directories are skipped.
    """
    if not skills_dir.is_dir():
        return []
    skills: list[tuple[str, str]] = []
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        skill_file = entry / "SKILL.md"
        if not skill_file.is_file():
            continue
        try:
            parsed = parse_skill_frontmatter(
                skill_file.read_text(encoding="utf-8")
            )
            if parsed:
                skills.append(parsed)
        except OSError:
            continue
    return skills
