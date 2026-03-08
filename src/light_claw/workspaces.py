from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .models import WorkspaceRecord


def _slugify(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return normalized or fallback


def _owner_dir_name(owner_id: str) -> str:
    return _slugify(owner_id, "owner")


def _workspace_files(name: str, workspace_id: str) -> dict[str, str]:
    return {
        "AGENTS.md": "\n".join(
            [
                "# AGENTS.md",
                "",
                "You are the agent assigned to this workspace.",
                f"- Workspace name: {name}",
                f"- Workspace ID: {workspace_id}",
                "",
                "Before each task:",
                "- Read `./README.md` and the files under `./memory/`.",
                "- Treat `memory/*.md` as durable memory.",
                "- Use `memory/daily/YYYY-MM-DD.md` for temporary notes.",
                "- Keep edits minimal and traceable.",
                "- When you learn a durable fact about the user or project, update the right memory file.",
            ]
        )
        + "\n",
        "README.md": "\n".join(
            [
                f"# {name}",
                "",
                f"This is the isolated agent workspace `{workspace_id}`.",
                "",
                "Recommended usage:",
                "- Keep task-specific code and docs here.",
                "- Keep durable facts in `memory/`.",
                "- Let your selected CLI run inside this directory so `AGENTS.md` is in scope.",
            ]
        )
        + "\n",
        "memory/identity.md": "# Identity\n\n- Owner:\n- Mission:\n- Working style:\n",
        "memory/profile.md": "# Profile\n\n- Stable facts:\n- Preferences:\n",
        "memory/preferences.md": "# Preferences\n\n- Coding preferences:\n- Communication preferences:\n",
        "memory/projects.md": "# Projects\n\n- Active:\n- Backlog:\n",
        "memory/decisions.md": "# Decisions\n\n- \n",
        "memory/open_loops.md": "# Open Loops\n\n- \n",
        "memory/daily/README.md": "# Daily Notes\n\nUse one file per day for short-lived notes.\n",
    }


class WorkspaceManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(
        self, owner_id: str, name: str, existing_ids: Iterable[str], cli_provider: str
    ) -> WorkspaceRecord:
        workspace_name = name.strip() or "Workspace"
        existing = set(existing_ids)
        base_id = _slugify(workspace_name, "workspace")
        workspace_id = base_id
        index = 2
        while workspace_id in existing:
            workspace_id = f"{base_id}-{index}"
            index += 1

        owner_dir = self.root_dir / _owner_dir_name(owner_id)
        workspace_dir = owner_dir / workspace_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_workspace(workspace_dir, workspace_name, workspace_id)

        return WorkspaceRecord(
            owner_id=owner_id,
            workspace_id=workspace_id,
            name=workspace_name,
            path=workspace_dir,
            cli_provider=cli_provider,
            created_at=0.0,
            updated_at=0.0,
        )

    def ensure_workspace_layout(self, workspace: WorkspaceRecord) -> None:
        workspace.path.mkdir(parents=True, exist_ok=True)
        self._bootstrap_workspace(
            workspace.path,
            workspace.name,
            workspace.workspace_id,
        )

    def _bootstrap_workspace(
        self,
        workspace_dir: Path,
        workspace_name: str,
        workspace_id: str,
    ) -> None:
        for relative_path, content in _workspace_files(workspace_name, workspace_id).items():
            target = workspace_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text(content, encoding="utf-8")
