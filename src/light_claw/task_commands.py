from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Iterable, Optional

from .communication.events import InboundMessage
from .communication.sender import MessageSender
from .commands import Command
from .config import AgentSettings, Settings
from .models import (
    SCHEDULE_KIND_INTERVAL,
    TASK_STATUS_CANCELLED,
    ScheduledTaskRecord,
    TaskRunRecord,
    WorkspaceRecord,
    WorkspaceTaskRecord,
)
from .store import StateStore
from .task_executor import TaskExecutor


log = logging.getLogger("light_claw.task_commands")


class TaskCommandHandler:
    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings,
        store: StateStore,
        message_sender: MessageSender,
        task_executor: TaskExecutor,
        ensure_workspace: Callable[[], WorkspaceRecord],
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.message_sender = message_sender
        self.task_executor = task_executor
        self.ensure_workspace = ensure_workspace

    async def handle(
        self,
        message: InboundMessage,
        command: Command,
    ) -> Optional[str]:
        if command.kind == "task_list":
            workspace = self.ensure_workspace()
            tasks = self.store.list_workspace_tasks(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
            )
            return self._render_task_list(tasks)
        if command.kind == "task_status":
            if not command.argument:
                return "Usage: /task status <id|index>"
            workspace = self.ensure_workspace()
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
            workspace = self.ensure_workspace()
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
                    "First run: starting now",
                ]
            )
            self._record_command_observation(
                workspace=workspace,
                message=message,
                command_kind=command.kind,
                response=response,
                context_key=task.task_id,
            )
            await self.message_sender.send_text(message.reply_target, response)
            self._start_background_task(
                self.task_executor.execute_workspace_task(
                    task,
                    trigger_source="task_create",
                    reschedule_seconds=(
                        self.settings.task_heartbeat_interval_seconds
                        if self.settings.task_heartbeat_enabled
                        else None
                    ),
                    announce_start=False,
                    deliver_result=True,
                ),
                description="task_create {}".format(task.task_id),
            )
            return None
        if command.kind == "task_cancel":
            if not command.argument:
                return "Usage: /task cancel <id|index>"
            workspace = self.ensure_workspace()
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
            workspace = self.ensure_workspace()
            schedules = self.store.list_scheduled_tasks(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
            )
            return self._render_schedule_list(schedules)
        if command.kind == "cron_every":
            if not command.argument:
                return "Usage: /cron every <seconds> <task_id>"
            workspace = self.ensure_workspace()
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
            workspace = self.ensure_workspace()
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
        return None

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

    @staticmethod
    def _start_background_task(coro, *, description: str) -> None:
        task = asyncio.create_task(coro)
        task.add_done_callback(
            lambda current: TaskCommandHandler._log_background_task_result(
                current,
                description=description,
            )
        )

    @staticmethod
    def _log_background_task_result(task: asyncio.Task[object], *, description: str) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("background task failed: %s", description)

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
