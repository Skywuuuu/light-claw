from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable, Optional

from .models import WorkspaceRecord


def _slugify(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return normalized or fallback


def _owner_dir_name(owner_id: str) -> str:
    return _slugify(owner_id, "owner")


def _agent_dir_name(agent_id: str) -> str:
    return _slugify(agent_id, "agent")


def workspace_relative_dir(agent_id: str, owner_id: str, workspace_id: str) -> Path:
    """Return the relative directory used for a workspace on disk."""

    return Path(_agent_dir_name(agent_id)) / _owner_dir_name(owner_id) / workspace_id


def _workspace_files(
    name: str,
    workspace_id: str,
    *,
    agent_id: str,
    agent_name: str,
    skills_path: Optional[Path],
    mcp_config_path: Optional[Path],
) -> dict[str, str]:
    agent_profile = {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "workspace_id": workspace_id,
        "skills_path": str(skills_path) if skills_path else None,
        "mcp_config_path": str(mcp_config_path) if mcp_config_path else None,
    }
    return {
        "AGENTS.md": "\n".join(
            [
                "# AGENTS.md",
                "",
                "You are the agent assigned to this workspace.",
                f"- Agent ID: {agent_id}",
                f"- Agent name: {agent_name}",
                f"- Workspace name: {name}",
                f"- Workspace ID: {workspace_id}",
                "",
                "Before each task:",
                "- Read `./README.md` and the files under `./memory/`.",
                "- Read `./.light-claw/agent.json` for the agent binding.",
                "- Read `./.light-claw/skills.md` and `./.light-claw/mcp.md` before using custom tools.",
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
                f"It belongs to agent `{agent_id}` ({agent_name}).",
                "",
                "Recommended usage:",
                "- Keep task-specific code and docs here.",
                "- Keep durable facts in `memory/`.",
                "- Let your selected CLI run inside this directory so `AGENTS.md` is in scope.",
            ]
        )
        + "\n",
        ".light-claw/agent.json": json.dumps(agent_profile, indent=2) + "\n",
        ".light-claw/skills.md": "\n".join(
            [
                "# Agent Skills",
                "",
                "This file is the workspace-local skill policy for the current agent.",
                "Only use skills that are explicitly enabled here or by the referenced source file.",
                "",
                "Configured source:",
                str(skills_path) if skills_path else "(none configured)",
            ]
        )
        + "\n",
        ".light-claw/mcp.md": "\n".join(
            [
                "# Agent MCP",
                "",
                "This file records the MCP/tool profile allowed for the current agent.",
                "Treat it as the agent-local MCP contract before calling external tools.",
                "",
                "Configured source:",
                str(mcp_config_path) if mcp_config_path else "(none configured)",
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
        self,
        agent_id: str,
        owner_id: str,
        name: str,
        existing_ids: Iterable[str],
        cli_provider: str,
        agent_name: str,
        skills_path: Optional[Path] = None,
        mcp_config_path: Optional[Path] = None,
    ) -> WorkspaceRecord:
        workspace_name = name.strip() or "Workspace"
        existing = set(existing_ids)
        base_id = _slugify(workspace_name, "workspace")
        workspace_id = base_id
        index = 2
        while workspace_id in existing:
            workspace_id = f"{base_id}-{index}"
            index += 1

        workspace_dir = self.root_dir / workspace_relative_dir(
            agent_id,
            owner_id,
            workspace_id,
        )
        workspace_dir.mkdir(parents=True, exist_ok=True)
        self._bootstrap_workspace(
            workspace_dir,
            workspace_name,
            workspace_id,
            agent_id=agent_id,
            agent_name=agent_name,
            skills_path=skills_path,
            mcp_config_path=mcp_config_path,
        )

        return WorkspaceRecord(
            agent_id=agent_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            name=workspace_name,
            path=workspace_dir,
            cli_provider=cli_provider,
            created_at=0.0,
            updated_at=0.0,
        )

    def ensure_workspace_layout(
        self,
        workspace: WorkspaceRecord,
        *,
        agent_name: str,
        skills_path: Optional[Path] = None,
        mcp_config_path: Optional[Path] = None,
    ) -> None:
        workspace.path.mkdir(parents=True, exist_ok=True)
        self._bootstrap_workspace(
            workspace.path,
            workspace.name,
            workspace.workspace_id,
            agent_id=workspace.agent_id,
            agent_name=agent_name,
            skills_path=skills_path,
            mcp_config_path=mcp_config_path,
        )

    def _bootstrap_workspace(
        self,
        workspace_dir: Path,
        workspace_name: str,
        workspace_id: str,
        *,
        agent_id: str,
        agent_name: str,
        skills_path: Optional[Path],
        mcp_config_path: Optional[Path],
    ) -> None:
        for relative_path, content in _workspace_files(
            workspace_name,
            workspace_id,
            agent_id=agent_id,
            agent_name=agent_name,
            skills_path=skills_path,
            mcp_config_path=mcp_config_path,
        ).items():
            target = workspace_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.exists():
                target.write_text(content, encoding="utf-8")
