from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .archive import WorkspaceArchiveService
from .chat import ChatObserver, ChatService
from .config import APP_NAME, AgentSettings, Settings
from .cron import CronService
from .integrations.feishu import FeishuClient
from .heartbeat import WorkspaceHeartbeatService
from .store import StateStore
from .task_executor import TaskExecutor
from .providers import CliRunnerRegistry
from .workspaces import WorkspaceManager


log = logging.getLogger("light_claw.runtime_services")


@dataclass
class AgentRuntime:
    agent: AgentSettings
    cli_registry: CliRunnerRegistry
    feishu_client: FeishuClient
    task_executor: TaskExecutor
    chat_service: ChatService


@dataclass
class RuntimeServices:
    settings: Settings
    store: StateStore
    workspace_manager: WorkspaceManager
    archive_service: WorkspaceArchiveService | None
    heartbeat_service: WorkspaceHeartbeatService | None
    cron_service: CronService | None
    health: "RuntimeHealth"
    agent_runtimes: dict[str, AgentRuntime]


class RuntimeHealth(ChatObserver):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started_at = time.time()
        self.archive_running = False
        self.archive_last_success_at: float | None = None
        self.archive_last_error: str | None = None
        self.heartbeat_running = False
        self.heartbeat_last_success_at: float | None = None
        self.heartbeat_last_error: str | None = None
        self.cron_running = False
        self.cron_last_success_at: float | None = None
        self.cron_last_error: str | None = None
        self.background_error_count = 0
        self.message_counts = {
            "received": 0,
            "completed": 0,
            "failed": 0,
        }
        self.outcome_counts: dict[str, int] = {}
        self.agent_states = {
            agent.agent_id: {
                "app_id": agent.feishu_app_id,
                "connected": False,
                "last_event_at": None,
            }
            for agent in settings.agents
        }

    def mark_archive_started(self) -> None:
        self.archive_running = True

    def mark_archive_stopped(self) -> None:
        self.archive_running = False

    def mark_archive_synced(self) -> None:
        self.archive_last_success_at = time.time()
        self.archive_last_error = None

    def mark_archive_error(self, exc: Exception) -> None:
        self.archive_last_error = str(exc)

    def mark_heartbeat_started(self) -> None:
        self.heartbeat_running = True

    def mark_heartbeat_stopped(self) -> None:
        self.heartbeat_running = False

    def mark_heartbeat_tick(self) -> None:
        self.heartbeat_last_success_at = time.time()
        self.heartbeat_last_error = None

    def mark_heartbeat_error(self, exc: Exception) -> None:
        self.heartbeat_last_error = str(exc)

    def mark_cron_started(self) -> None:
        self.cron_running = True

    def mark_cron_stopped(self) -> None:
        self.cron_running = False

    def mark_cron_tick(self) -> None:
        self.cron_last_success_at = time.time()
        self.cron_last_error = None

    def mark_cron_error(self, exc: Exception) -> None:
        self.cron_last_error = str(exc)

    def mark_agent_connection(self, agent_id: str, connected: bool) -> None:
        state = self.agent_states.setdefault(
            agent_id,
            {"app_id": None, "connected": False, "last_event_at": None},
        )
        state["connected"] = connected

    def mark_agent_event(self, agent_id: str) -> None:
        state = self.agent_states.setdefault(
            agent_id,
            {"app_id": None, "connected": False, "last_event_at": None},
        )
        state["last_event_at"] = time.time()

    def mark_background_error(self) -> None:
        self.background_error_count += 1

    def on_message_received(self, agent_id: str) -> None:
        self.message_counts["received"] += 1
        self.mark_agent_event(agent_id)

    def on_message_completed(
        self,
        agent_id: str,
        *,
        outcome: str,
        latency_ms: int,
    ) -> None:
        self.message_counts["completed"] += 1
        self.outcome_counts[outcome] = self.outcome_counts.get(outcome, 0) + 1
        log.info(
            "message completed agent=%s outcome=%s latency_ms=%s",
            agent_id,
            outcome,
            latency_ms,
        )

    def on_message_failed(self, agent_id: str, *, latency_ms: int) -> None:
        self.message_counts["failed"] += 1
        log.exception("message failed agent=%s latency_ms=%s", agent_id, latency_ms)

    def snapshot(self, *, store_ok: bool) -> dict[str, Any]:
        if self.settings.feishu_enabled and self.settings.feishu_event_mode == "long_connection":
            agents_ready = all(
                bool(state["connected"]) for state in self.agent_states.values()
            )
        else:
            agents_ready = True
        ready = store_ok and agents_ready and (
            (not self.settings.archive_enabled) or self.archive_running
        )
        ready = ready and ((not self.settings.task_heartbeat_enabled) or self.heartbeat_running)
        ready = ready and ((not self.settings.cron_enabled) or self.cron_running)
        return {
            "app": APP_NAME,
            "started_at": self.started_at,
            "uptime_seconds": int(time.time() - self.started_at),
            "event_mode": self.settings.feishu_event_mode,
            "store_ok": store_ok,
            "archive": {
                "enabled": self.settings.archive_enabled,
                "running": self.archive_running,
                "last_success_at": self.archive_last_success_at,
                "last_error": self.archive_last_error,
            },
            "heartbeat": {
                "enabled": self.settings.task_heartbeat_enabled,
                "running": self.heartbeat_running,
                "last_success_at": self.heartbeat_last_success_at,
                "last_error": self.heartbeat_last_error,
            },
            "cron": {
                "enabled": self.settings.cron_enabled,
                "running": self.cron_running,
                "last_success_at": self.cron_last_success_at,
                "last_error": self.cron_last_error,
            },
            "agents": self.agent_states,
            "messages": self.message_counts,
            "outcomes": self.outcome_counts,
            "background_error_count": self.background_error_count,
            "ready": ready,
        }


def build_services(settings: Settings) -> RuntimeServices:
    store = StateStore(settings.database_path)
    recovered_runs = store.recover_orphaned_task_runs()
    if recovered_runs:
        log.warning("recovered %s orphaned task runs during startup", recovered_runs)
    store.prune_inbound_messages(settings.inbound_message_ttl_seconds)
    workspace_manager = WorkspaceManager(settings.workspaces_dir)
    health = RuntimeHealth(settings)
    archive_service = None
    heartbeat_service = None
    cron_service = None
    if settings.archive_enabled:
        archive_service = WorkspaceArchiveService(
            store=store,
            archive_root=settings.archive_dir,
            interval_seconds=settings.archive_interval_seconds,
            inbound_message_ttl_seconds=settings.inbound_message_ttl_seconds,
            on_sync_success=health.mark_archive_synced,
            on_sync_error=health.mark_archive_error,
        )

    agent_runtimes: dict[str, AgentRuntime] = {}
    task_executors: dict[str, TaskExecutor] = {}
    for agent in settings.agents:
        cli_registry = CliRunnerRegistry.from_settings(settings, agent)
        feishu_client = FeishuClient(
            app_id=agent.feishu_app_id or "",
            app_secret=agent.feishu_app_secret or "",
        )
        task_executor = TaskExecutor(
            settings=settings,
            agent=agent,
            store=store,
            cli_registry=cli_registry,
            feishu_client=feishu_client,
        )
        chat_service = ChatService(
            settings=settings,
            agent=agent,
            store=store,
            workspace_manager=workspace_manager,
            cli_registry=cli_registry,
            feishu_client=feishu_client,
            task_executor=task_executor,
            archive_service=archive_service,
            observer=health,
        )
        agent_runtimes[agent.agent_id] = AgentRuntime(
            agent=agent,
            cli_registry=cli_registry,
            feishu_client=feishu_client,
            task_executor=task_executor,
            chat_service=chat_service,
        )
        task_executors[agent.agent_id] = task_executor

    if settings.task_heartbeat_enabled:
        heartbeat_service = WorkspaceHeartbeatService(
            store=store,
            executors=task_executors,
            interval_seconds=settings.task_heartbeat_interval_seconds,
            on_tick_success=health.mark_heartbeat_tick,
            on_tick_error=health.mark_heartbeat_error,
        )
    if settings.cron_enabled:
        cron_service = CronService(
            store=store,
            executors=task_executors,
            poll_interval_seconds=settings.cron_poll_interval_seconds,
            on_tick_success=health.mark_cron_tick,
            on_tick_error=health.mark_cron_error,
        )

    return RuntimeServices(
        settings=settings,
        store=store,
        workspace_manager=workspace_manager,
        archive_service=archive_service,
        heartbeat_service=heartbeat_service,
        cron_service=cron_service,
        health=health,
        agent_runtimes=agent_runtimes,
    )


async def start_managed_services(services: RuntimeServices) -> None:
    if services.archive_service is not None:
        await services.archive_service.start()
        services.health.mark_archive_started()
    if services.heartbeat_service is not None:
        await services.heartbeat_service.start()
        services.health.mark_heartbeat_started()
    if services.cron_service is not None:
        await services.cron_service.start()
        services.health.mark_cron_started()


async def shutdown_services(services: RuntimeServices) -> None:
    if services.archive_service is not None:
        await services.archive_service.stop()
        services.health.mark_archive_stopped()
    if services.heartbeat_service is not None:
        await services.heartbeat_service.stop()
        services.health.mark_heartbeat_stopped()
    if services.cron_service is not None:
        await services.cron_service.stop()
        services.health.mark_cron_stopped()
    for runtime in services.agent_runtimes.values():
        await runtime.feishu_client.close()
    services.store.close()
