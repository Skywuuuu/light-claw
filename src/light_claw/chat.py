from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, Iterable, Optional, Protocol

from .archive import WorkspaceArchiveService
from .cli_runners import CliRunnerRegistry
from .commands import Command, help_text, parse_command
from .config import AgentSettings, Settings
from .feishu import FeishuClient
from .models import (
    SCHEDULE_KIND_INTERVAL,
    TASK_STATUS_CANCELLED,
    FeishuInboundMessage,
    ScheduledTaskRecord,
    TaskRunRecord,
    WorkspaceRecord,
    WorkspaceTaskRecord,
)
from .store import StateStore
from .task_executor import TaskExecutor
from .workspaces import WorkspaceManager


log = logging.getLogger("light_claw.chat")


class ChatObserver(Protocol):
    def on_message_received(self, agent_id: str) -> None:
        ...

    def on_message_completed(
        self,
        agent_id: str,
        *,
        outcome: str,
        latency_ms: int,
    ) -> None:
        ...

    def on_message_failed(self, agent_id: str, *, latency_ms: int) -> None:
        ...


class ChatService:
    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings,
        store: StateStore,
        workspace_manager: WorkspaceManager,
        cli_registry: CliRunnerRegistry,
        feishu_client: FeishuClient,
        task_executor: TaskExecutor,
        archive_service: WorkspaceArchiveService | None = None,
        observer: ChatObserver | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.workspace_manager = workspace_manager
        self.cli_registry = cli_registry
        self.feishu_client = feishu_client
        self.task_executor = task_executor
        self.archive_service = archive_service
        self.observer = observer
        self._conversation_locks: Dict[str, asyncio.Lock] = {}

    async def handle_message(self, message: FeishuInboundMessage) -> None:
        started_at = asyncio.get_running_loop().time()
        if self.observer is not None:
            self.observer.on_message_received(self.agent.agent_id)
        if not self._is_allowed_user(message.owner_id):
            self._record_completion(started_at, outcome="ignored")
            return
        dedupe_key = "feishu:{}:{}".format(message.bot_app_id, message.message_id)
        if not self.store.remember_inbound_message(self.agent.agent_id, dedupe_key):
            self._record_completion(started_at, outcome="duplicate")
            return

        lock_key = "{}:{}".format(message.conversation_id, message.owner_id)
        lock = self._conversation_locks.setdefault(lock_key, asyncio.Lock())
        outcome = "handled"
        try:
            async with lock:
                command = parse_command(message.content)
                if command:
                    response = await self._handle_command(message, command)
                    outcome = "command"
                    if response:
                        await self.feishu_client.send_text(message.reply_target, response)
                    return
                outcome = await self._handle_prompt(message)
        except Exception:
            latency_ms = int((asyncio.get_running_loop().time() - started_at) * 1000)
            if self.observer is not None:
                self.observer.on_message_failed(self.agent.agent_id, latency_ms=latency_ms)
            raise
        finally:
            if not lock.locked():
                self._conversation_locks.pop(lock_key, None)
        self._record_completion(started_at, outcome=outcome)

    def _record_completion(self, started_at: float, *, outcome: str) -> None:
        if self.observer is None:
            return
        latency_ms = int((asyncio.get_running_loop().time() - started_at) * 1000)
        self.observer.on_message_completed(
            self.agent.agent_id,
            outcome=outcome,
            latency_ms=latency_ms,
        )

    async def _handle_command(
        self, message: FeishuInboundMessage, command: Command
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
            workspace = self._get_workspace()
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
            workspace = self._get_workspace()
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
            workspace = self._ensure_workspace()
            return self._render_cli_list(workspace.cli_provider)
        if command.kind == "cli_current":
            workspace = self._ensure_workspace()
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
            workspace = self._ensure_workspace()
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
        if command.kind == "task_list":
            workspace = self._ensure_workspace()
            tasks = self.store.list_workspace_tasks(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
            )
            return self._render_task_list(tasks)
        if command.kind == "task_status":
            if not command.argument:
                return "Usage: /task status <id|index>"
            workspace = self._ensure_workspace()
            tasks = self.store.list_workspace_tasks(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
            )
            task = self._resolve_task_target(tasks, command.argument)
            if task is None:
                return "Task not found. Use `/task list` first."
            latest_run = self.store.get_latest_task_run(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
                task.task_id,
            )
            return self._render_task_status(workspace, task, latest_run)
        if command.kind == "task_create":
            if not command.argument:
                return "Usage: /task create <prompt>"
            workspace = self._ensure_workspace()
            task = self.store.create_workspace_task(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
                command.argument,
                notify_conversation_id=message.conversation_id,
                notify_owner_id=message.owner_id,
                notify_receive_id=message.reply_target.receive_id,
                notify_receive_id_type=message.reply_target.receive_id_type,
                next_run_at=time.time(),
            )
            response = "\n".join(
                [
                    "Task created.",
                    "{} ({})".format(task.title, task.task_id),
                    "Workspace: {} ({})".format(workspace.name, workspace.workspace_id),
                    "Next run: now",
                ]
            )
            self._record_command_observation(
                workspace=workspace,
                message=message,
                command_kind=command.kind,
                response=response,
                context_key=task.task_id,
            )
            return response
        if command.kind == "task_cancel":
            if not command.argument:
                return "Usage: /task cancel <id|index>"
            workspace = self._ensure_workspace()
            tasks = self.store.list_workspace_tasks(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
            )
            task = self._resolve_task_target(tasks, command.argument)
            if task is None:
                return "Task not found. Use `/task list` first."
            self.store.update_workspace_task(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
                task.task_id,
                status=TASK_STATUS_CANCELLED,
                next_run_at=None,
            )
            response = "Task cancelled.\n{} ({})".format(task.title, task.task_id)
            self._record_command_observation(
                workspace=workspace,
                message=message,
                command_kind=command.kind,
                response=response,
                context_key=task.task_id,
            )
            return response
        if command.kind == "cron_list":
            workspace = self._ensure_workspace()
            schedules = self.store.list_scheduled_tasks(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
            )
            return self._render_schedule_list(schedules)
        if command.kind == "cron_every":
            if not command.argument:
                return "Usage: /cron every <seconds> <task_id>"
            workspace = self._ensure_workspace()
            seconds, task_ref = self._parse_cron_every_argument(command.argument)
            if seconds is None or not task_ref:
                return "Usage: /cron every <seconds> <task_id>"
            tasks = self.store.list_workspace_tasks(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
            )
            task = self._resolve_task_target(tasks, task_ref)
            if task is None:
                return "Task not found. Use `/task list` first."
            schedule = self.store.create_scheduled_task(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
                task.task_id,
                kind=SCHEDULE_KIND_INTERVAL,
                interval_seconds=seconds,
                next_run_at=time.time() + seconds,
            )
            response = "\n".join(
                [
                    "Cron schedule created.",
                    "{} ({})".format(task.title, task.task_id),
                    "Schedule: {} every {}s".format(schedule.schedule_id, seconds),
                ]
            )
            self._record_command_observation(
                workspace=workspace,
                message=message,
                command_kind=command.kind,
                response=response,
                context_key=schedule.schedule_id,
            )
            return response
        if command.kind == "cron_remove":
            if not command.argument:
                return "Usage: /cron remove <id|index>"
            workspace = self._ensure_workspace()
            schedules = self.store.list_scheduled_tasks(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
            )
            schedule = self._resolve_schedule_target(schedules, command.argument)
            if schedule is None:
                return "Cron schedule not found. Use `/cron list` first."
            self.store.remove_scheduled_task(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
                schedule.schedule_id,
            )
            response = "Cron schedule removed.\n{}".format(schedule.schedule_id)
            self._record_command_observation(
                workspace=workspace,
                message=message,
                command_kind=command.kind,
                response=response,
                context_key=schedule.schedule_id,
            )
            return response
        if command.kind == "invalid":
            return "Unknown command. Use `/help`."
        return None

    async def _handle_prompt(self, message: FeishuInboundMessage) -> str:
        workspace = self._ensure_workspace()
        result = await self.task_executor.execute_prompt(
            workspace=workspace,
            prompt=message.content,
            conversation_id=message.conversation_id,
            conversation_owner_id=message.owner_id,
            reply_target=message.reply_target,
            announce_start=True,
            deliver_result=True,
        )
        if result.status != "succeeded":
            return "cli_failed"
        return "prompt"

    def _ensure_workspace(self) -> WorkspaceRecord:
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

    def _ensure_workspace_layout(self, workspace: WorkspaceRecord) -> None:
        self.workspace_manager.ensure_workspace_layout(
            workspace,
            agent_name=self.agent.name,
            skills_path=self.agent.skills_path,
            mcp_config_path=self.agent.mcp_config_path,
        )

    def _get_workspace(self) -> WorkspaceRecord | None:
        return self.store.get_agent_workspace(self.agent.agent_id)

    def _record_command_observation(
        self,
        *,
        workspace: WorkspaceRecord,
        message: FeishuInboundMessage,
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

    def _render_task_list(self, tasks: Iterable[WorkspaceTaskRecord]) -> str:
        items = list(tasks)
        if not items:
            return "Tasks:\n(no tasks)"
        lines = ["Tasks:"]
        for index, task in enumerate(items, start=1):
            lines.append(
                "{}. {} ({}) [{}]".format(
                    index,
                    task.title,
                    task.task_id,
                    task.status,
                )
            )
            if task.next_run_at is not None:
                lines.append("Next run at: {}".format(int(task.next_run_at)))
        return "\n".join(lines)

    def _render_task_status(
        self,
        workspace: WorkspaceRecord,
        task: WorkspaceTaskRecord,
        latest_run: TaskRunRecord | None,
    ) -> str:
        lines = [
            "Task status:",
            "{} ({})".format(task.title, task.task_id),
            "Workspace: {} ({})".format(workspace.name, workspace.workspace_id),
            "Status: {}".format(task.status),
            "Created at: {}".format(int(task.created_at)),
        ]
        if task.last_run_at is not None:
            lines.append("Last run at: {}".format(int(task.last_run_at)))
        else:
            lines.append("Last run at: (never)")
        if task.next_run_at is not None:
            lines.append("Next run at: {}".format(int(task.next_run_at)))
        else:
            lines.append("Next run at: (none)")
        if latest_run is not None:
            lines.extend(
                [
                    "Latest run: {} [{}]".format(latest_run.run_id, latest_run.status),
                    "Trigger: {}".format(latest_run.trigger_source),
                    "Started at: {}".format(int(latest_run.started_at)),
                ]
            )
            if latest_run.finished_at is not None:
                lines.append("Finished at: {}".format(int(latest_run.finished_at)))
        if task.last_result_excerpt:
            lines.extend(["Last result:", task.last_result_excerpt])
        if task.last_error_message:
            lines.extend(["Last error:", task.last_error_message])
        return "\n".join(lines)

    def _render_schedule_list(self, schedules: Iterable[ScheduledTaskRecord]) -> str:
        items = list(schedules)
        if not items:
            return "Cron schedules:\n(no schedules)"
        lines = ["Cron schedules:"]
        for index, schedule in enumerate(items, start=1):
            detail = (
                "every {}s".format(schedule.interval_seconds)
                if schedule.interval_seconds
                else schedule.cron_expr or schedule.kind
            )
            lines.append(
                "{}. {} -> task {} [{}]".format(
                    index,
                    schedule.schedule_id,
                    schedule.task_id,
                    detail,
                )
            )
        return "\n".join(lines)

    @staticmethod
    def _parse_cron_every_argument(raw: str) -> tuple[int | None, str | None]:
        parts = raw.split(maxsplit=1)
        if len(parts) != 2 or not parts[0].isdigit():
            return None, None
        seconds = int(parts[0])
        if seconds <= 0:
            return None, None
        return seconds, parts[1].strip() or None

    def _resolve_task_target(
        self,
        tasks: Iterable[WorkspaceTaskRecord],
        target: str,
    ) -> Optional[WorkspaceTaskRecord]:
        items = list(tasks)
        raw = target.strip()
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(items):
                return items[index - 1]
        for task in items:
            if task.task_id == raw:
                return task
        return None

    def _resolve_schedule_target(
        self,
        schedules: Iterable[ScheduledTaskRecord],
        target: str,
    ) -> Optional[ScheduledTaskRecord]:
        items = list(schedules)
        raw = target.strip()
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(items):
                return items[index - 1]
        for schedule in items:
            if schedule.schedule_id == raw:
                return schedule
        return None

    def _is_allowed_user(self, owner_id: str) -> bool:
        allow_from = self.agent.allow_from.strip()
        if not allow_from or allow_from == "*":
            return True
        allowed = {value.strip() for value in allow_from.split(",") if value.strip()}
        return owner_id in allowed
