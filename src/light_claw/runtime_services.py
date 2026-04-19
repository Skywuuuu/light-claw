from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .chat import ChatObserver, ChatService
from .communication.base import BaseCommunicationChannel
from .communication.feishu import FeishuCommunicationChannel
from .config import APP_NAME, AgentSettings, Settings
from .runtime import CliRuntimeRegistry
from .store import StateStore
from .task_executor import TaskExecutor
from .workspaces import WorkspaceManager


log = logging.getLogger("light_claw.runtime_services")


@dataclass
class AgentRuntime:
    agent: AgentSettings
    cli_registry: CliRuntimeRegistry
    communication_channel: BaseCommunicationChannel
    task_executor: TaskExecutor
    chat_service: ChatService


@dataclass
class RuntimeServices:
    settings: Settings
    store: StateStore
    workspace_manager: WorkspaceManager
    health: "RuntimeHealth"
    agent_runtimes: dict[str, AgentRuntime]


class RuntimeHealth(ChatObserver):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started_at = time.time()
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
        ready = store_ok and agents_ready
        return {
            "app": APP_NAME,
            "started_at": self.started_at,
            "uptime_seconds": int(time.time() - self.started_at),
            "event_mode": self.settings.feishu_event_mode,
            "store_ok": store_ok,
            "agents": self.agent_states,
            "messages": self.message_counts,
            "outcomes": self.outcome_counts,
            "background_error_count": self.background_error_count,
            "ready": ready,
        }


def build_services(settings: Settings) -> RuntimeServices:
    store = StateStore(settings.database_path)
    store.prune_inbound_messages(settings.inbound_message_ttl_seconds)
    workspace_manager = WorkspaceManager(settings.workspaces_dir)
    health = RuntimeHealth(settings)

    agent_runtimes: dict[str, AgentRuntime] = {}
    for agent in settings.agents:
        cli_registry = CliRuntimeRegistry.from_settings(settings, agent)
        communication_channel = FeishuCommunicationChannel(
            agent_id=agent.agent_id,
            app_id=agent.feishu_app_id or "",
            app_secret=agent.feishu_app_secret or "",
            on_running_change=health.mark_agent_connection,
        )
        task_executor = TaskExecutor(
            settings=settings,
            agent=agent,
            store=store,
            cli_registry=cli_registry,
            communication_channel=communication_channel,
        )
        chat_service = ChatService(
            settings=settings,
            agent=agent,
            store=store,
            workspace_manager=workspace_manager,
            cli_registry=cli_registry,
            communication_channel=communication_channel,
            task_executor=task_executor,
            observer=health,
        )
        agent_runtimes[agent.agent_id] = AgentRuntime(
            agent=agent,
            cli_registry=cli_registry,
            communication_channel=communication_channel,
            task_executor=task_executor,
            chat_service=chat_service,
        )

    return RuntimeServices(
        settings=settings,
        store=store,
        workspace_manager=workspace_manager,
        health=health,
        agent_runtimes=agent_runtimes,
    )


async def start_managed_services(services: RuntimeServices) -> None:
    pass


async def shutdown_services(services: RuntimeServices) -> None:
    for runtime in services.agent_runtimes.values():
        await runtime.communication_channel.close()
    services.store.close()
