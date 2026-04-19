from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from .models import WorkspaceRecord

DEFAULT_WORKSPACE_ID = "default"
DEFAULT_WORKSPACE_OWNER = "__agent__"


def _slugify(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return normalized or fallback


def _agent_dir_name(agent_id: str) -> str:
    return _slugify(agent_id, "agent")


def workspace_relative_dir(agent_id: str) -> Path:
    """Return the relative directory used for a workspace on disk."""

    return Path(_agent_dir_name(agent_id))


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
    }


class WorkspaceManager:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir.resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)

    def create_workspace(
        self,
        agent_id: str,
        name: str,
        cli_provider: str,
        agent_name: str,
        skills_path: Optional[Path] = None,
        mcp_config_path: Optional[Path] = None,
    ) -> WorkspaceRecord:
        workspace_name = name.strip() or "Workspace"
        workspace_id = DEFAULT_WORKSPACE_ID

        workspace_dir = self.root_dir / workspace_relative_dir(agent_id)
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
            owner_id=DEFAULT_WORKSPACE_OWNER,
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
