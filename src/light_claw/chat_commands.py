from __future__ import annotations

from typing import Optional

from .communication.base import BaseCommunicationChannel
from .communication.messages import InboundMessage
from .commands import Command, help_text
from .config import AgentSettings, Settings
from .models import WorkspaceRecord
from .runtime import CliRuntimeRegistry
from .store import StateStore
from .task_executor import TaskExecutor
from .workspaces import WorkspaceManager


class ChatCommandHandler:
    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings,
        store: StateStore,
        workspace_manager: WorkspaceManager,
        cli_registry: CliRuntimeRegistry,
        communication_channel: BaseCommunicationChannel,
        task_executor: TaskExecutor,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.workspace_manager = workspace_manager
        self.cli_registry = cli_registry
        self.communication_channel = communication_channel
        self.task_executor = task_executor

    async def handle(
        self,
        message: InboundMessage,
        command: Command,
    ) -> Optional[str]:
        if command.kind == "help":
            return help_text()
        if command.kind == "reset":
            workspace = self.get_workspace()
            self.store.clear_session(
                self.agent.agent_id,
                message.conversation_id,
                message.owner_id,
            )
            if workspace is not None:
                self.task_executor.clear_observations(
                    workspace=workspace,
                    conversation_id=message.conversation_id,
                    conversation_owner_id=message.owner_id,
                )
            return "Current workspace session cleared. The next message will start a new session."
        if command.kind == "cli_list":
            workspace = self.ensure_workspace()
            return self._render_cli_list(workspace.cli_provider)
        if command.kind == "cli_current":
            workspace = self.ensure_workspace()
            provider = self.cli_registry.get_provider(workspace.cli_provider)
            return "\n".join(
                [
                    "Current CLI provider:",
                    "{} ({})".format(
                        provider.display_name if provider else workspace.cli_provider,
                        workspace.cli_provider,
                    ),
                    "Workspace: {} ({})".format(workspace.name, workspace.workspace_id),
                ]
            )
        if command.kind == "cli_use":
            if not command.argument:
                return "Usage: /cli use <provider>"
            workspace = self.ensure_workspace()
            previous_provider = workspace.cli_provider
            ok, reason = self.cli_registry.validate_selectable(command.argument)
            if not ok:
                return reason
            updated = self.store.set_workspace_cli_provider(
                workspace.agent_id,
                workspace.owner_id,
                workspace.workspace_id,
                command.argument.strip().lower(),
            )
            if updated is None:
                return "Failed to update workspace CLI provider."
            self._ensure_workspace_layout(updated)
            session_reset = previous_provider != updated.cli_provider
            if session_reset:
                self.store.clear_workspace_sessions(
                    updated.agent_id,
                    updated.workspace_id,
                )
                self.task_executor.clear_workspace_observations(workspace=updated)
            response = "\n".join(
                ["CLI provider updated.", "{} now uses `{}`.".format(updated.name, updated.cli_provider)]
                + (
                    [
                        "Existing workspace CLI sessions were cleared so the new provider starts fresh."
                    ]
                    if session_reset
                    else []
                )
            )
            self._record_command_observation(
                workspace=updated,
                message=message,
                command_kind=command.kind,
                response=response,
                context_key=updated.cli_provider,
            )
            return response
        if command.kind == "invalid":
            return "Unknown command. Use `/help`."
        return None

    def ensure_workspace(self) -> WorkspaceRecord:
        workspace = self.store.get_agent_workspace(self.agent.agent_id)
        if workspace is not None:
            self._ensure_workspace_layout(workspace)
            return workspace
        created = self.workspace_manager.create_workspace(
            agent_id=self.agent.agent_id,
            name=self.agent.default_workspace_name,
            cli_provider=self.cli_registry.default_provider_id(
                self.agent.default_cli_provider
            ),
            agent_name=self.agent.name,
            skills_path=self.agent.skills_path,
            mcp_config_path=self.agent.mcp_config_path,
        )
        created = self.store.create_workspace(created)
        self._ensure_workspace_layout(created)
        return created

    def get_workspace(self) -> WorkspaceRecord | None:
        return self.store.get_agent_workspace(self.agent.agent_id)

    def _ensure_workspace_layout(self, workspace: WorkspaceRecord) -> None:
        self.workspace_manager.ensure_workspace_layout(
            workspace,
            agent_name=self.agent.name,
            skills_path=self.agent.skills_path,
            mcp_config_path=self.agent.mcp_config_path,
        )

    def _record_command_observation(
        self,
        *,
        workspace: WorkspaceRecord,
        message: InboundMessage,
        command_kind: str,
        response: str,
        context_key: str | None = None,
    ) -> None:
        normalized_context = command_kind
        if context_key:
            normalized_context = "{}:{}".format(command_kind, context_key)
        self.task_executor.record_observation(
            workspace=workspace,
            conversation_id=message.conversation_id,
            conversation_owner_id=message.owner_id,
            kind="command_result",
            text=response,
            context_key=normalized_context,
        )

    def _render_cli_list(self, current_provider_id: str) -> str:
        lines = ["CLI providers:"]
        for provider in self.cli_registry.list_providers():
            marker = "->" if provider.provider_id == current_provider_id else "  "
            status = "ready" if provider.available else "reserved"
            lines.append(
                "{} {} ({}) [{}]".format(
                    marker,
                    provider.display_name,
                    provider.provider_id,
                    status,
                )
            )
            lines.append(provider.description)
        lines.append("Use `/cli use <provider>` to switch the current agent workspace.")
        return "\n".join(lines)
