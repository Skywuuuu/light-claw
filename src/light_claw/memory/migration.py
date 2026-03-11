from __future__ import annotations

from pathlib import Path

from .storage import global_memory_relative_path

LEGACY_MEMORY_TEMPLATES = {
    "memory/identity.md": "# Identity\n\n- Owner:\n- Mission:\n- Working style:\n",
    "memory/profile.md": "# Profile\n\n- Stable facts:\n- Preferences:\n",
    "memory/preferences.md": "# Preferences\n\n- Coding preferences:\n- Communication preferences:\n",
    "memory/projects.md": "# Projects\n\n- Active:\n- Backlog:\n",
    "memory/decisions.md": "# Decisions\n\n- \n",
    "memory/open_loops.md": "# Open Loops\n\n- \n",
}

_GLOBAL_MIGRATION_MARKER = "light-claw-global-memory-migration:v1"


def merge_legacy_global_memory(workspace_dir: Path) -> bool:
    """Append legacy workspace memory files into AGENTS.md once.

    Args:
        workspace_dir: Workspace root directory.
    """
    agents_path = workspace_dir / global_memory_relative_path()
    if not agents_path.exists():
        return False
    current = agents_path.read_text(encoding="utf-8")
    if _GLOBAL_MIGRATION_MARKER in current:
        return False
    sections: list[str] = []
    for relative_path, title in (
        ("memory/identity.md", "Migrated identity memory"),
        ("memory/profile.md", "Migrated profile memory"),
        ("memory/preferences.md", "Migrated user preferences"),
        ("memory/decisions.md", "Migrated decisions"),
        ("memory/open_loops.md", "Migrated open loops"),
    ):
        body = _legacy_memory_body(workspace_dir, relative_path)
        if not body:
            continue
        sections.extend([f"### {title}", body, ""])
    if not sections:
        return False
    block = "\n".join(
        [
            f"<!-- {_GLOBAL_MIGRATION_MARKER}:start -->",
            "",
            "## Migrated Legacy Memory",
            "",
            *sections,
            f"<!-- {_GLOBAL_MIGRATION_MARKER}:end -->",
            "",
        ]
    )
    agents_path.write_text(current.rstrip() + "\n\n" + block, encoding="utf-8")
    return True


def legacy_project_memory(workspace_dir: Path) -> str | None:
    """Return the non-template body of the legacy projects memory file."""
    return _legacy_memory_body(workspace_dir, "memory/projects.md")


def legacy_task_progress_note(workspace_dir: Path, task_id: str) -> str | None:
    """Return the old task note body if the legacy task note exists.

    Args:
        workspace_dir: Workspace root directory.
        task_id: Task id whose legacy note should be read.
    """
    path = workspace_dir / "memory" / "tasks" / f"{task_id}.md"
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    return content or None


def _legacy_memory_body(workspace_dir: Path, relative_path: str) -> str | None:
    path = workspace_dir / relative_path
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8")
    if content == LEGACY_MEMORY_TEMPLATES.get(relative_path):
        return None
    lines = content.splitlines()
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    body = "\n".join(lines).strip()
    return body or None
