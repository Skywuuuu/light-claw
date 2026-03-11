from __future__ import annotations

from typing import Optional

from .archive import WorkspaceArchiveService
from .communication.base import BaseCommunicationChannel
from .communication.messages import InboundMessage
from .commands import Command, help_text
from .config import AgentSettings, Settings
from .models import WorkspaceRecord
from .runtime import CliRuntimeRegistry
from .store import StateStore
from .task_commands import TaskCommandHandler
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
        archive_service: WorkspaceArchiveService | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.workspace_manager = workspace_manager
        self.cli_registry = cli_registry
        self.communication_channel = communication_channel
        self.task_executor = task_executor
        self.archive_service = archive_service
        self.task_commands = TaskCommandHandler(
            settings=settings,
            agent=agent,
            store=store,
            communication_channel=communication_channel,
            task_executor=task_executor,
            ensure_workspace=self.ensure_workspace,
        )

    async def handle(
        self,
        message: InboundMessage,
        command: Command,
    ) -> Optional[str]:
        if command.kind == "help":
            return help_text()
        if command.kind == "archive_current":
            return self._render_archive_status()
        if command.kind == "archive_daily":
            if not command.argument:
                return "Usage: /archive daily <HH:MM>"
            if self.archive_service is None:
                return "Archive service is disabled."
            try:
                daily_time = self.archive_service.update_daily_time(command.argument)
            except ValueError:
                return "Usage: /archive daily <HH:MM> (24-hour local time)"
            response = "\n".join(
                [
                    "Archive schedule updated.",
                    "Daily at {} (server local time).".format(daily_time),
                    "Scope: all agent workspaces.",
                ]
            )
            workspace = self.get_workspace()
            if workspace is not None:
                self._record_command_observation(
                    workspace=workspace,
                    message=message,
                    command_kind=command.kind,
                    response=response,
                    context_key=daily_time,
                )
            return response
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
            response = "\n".join(
                [
                    "CLI provider updated.",
                    "{} now uses `{}`.".format(updated.name, updated.cli_provider),
                ]
            )
            self._record_command_observation(
                workspace=updated,
                message=message,
                command_kind=command.kind,
                response=response,
                context_key=updated.cli_provider,
            )
            return response
        if command.kind.startswith("task_") or command.kind.startswith("cron_"):
            return await self.task_commands.handle(message, command)
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

    def _render_archive_status(self) -> str:
        if self.archive_service is None:
            return "Archive service is disabled."
        lines = [
            "Archive status:",
            "Scope: all agent workspaces",
            "Archive dir: {}".format(self.archive_service.archive_root),
        ]
        if self.archive_service.daily_time:
            lines.append(
                "Schedule: daily at {} (server local time)".format(
                    self.archive_service.daily_time
                )
            )
        else:
            lines.append(
                "Schedule: every {}s".format(self.archive_service.interval_seconds)
            )
        if self.archive_service.next_run_at is not None:
            lines.append("Next run at: {}".format(int(self.archive_service.next_run_at)))
        if self.archive_service.last_success_at is not None:
            lines.append(
                "Last success at: {}".format(int(self.archive_service.last_success_at))
            )
        else:
            lines.append("Last success at: (never)")
        if self.archive_service.last_error:
            lines.extend(["Last error:", self.archive_service.last_error])
        return "\n".join(lines)
