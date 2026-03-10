from __future__ import annotations

import asyncio
from typing import Dict, Protocol

from .archive import WorkspaceArchiveService
from .chat_commands import ChatCommandHandler
from .commands import parse_command
from .config import AgentSettings, Settings
from .integrations.feishu import FeishuClient
from .models import FeishuInboundMessage
from .providers import CliRunnerRegistry
from .store import StateStore
from .task_executor import TaskExecutor
from .workspaces import WorkspaceManager


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
        self.feishu_client = feishu_client
        self.task_executor = task_executor
        self.observer = observer
        self._conversation_locks: Dict[str, asyncio.Lock] = {}
        self.command_handler = ChatCommandHandler(
            settings=settings,
            agent=agent,
            store=store,
            workspace_manager=workspace_manager,
            cli_registry=cli_registry,
            feishu_client=feishu_client,
            task_executor=task_executor,
            archive_service=archive_service,
        )

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
                    response = await self.command_handler.handle(message, command)
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

    async def _handle_prompt(self, message: FeishuInboundMessage) -> str:
        workspace = self.command_handler.ensure_workspace()
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

    def _is_allowed_user(self, owner_id: str) -> bool:
        allow_from = self.agent.allow_from.strip()
        if not allow_from or allow_from == "*":
            return True
        allowed = {value.strip() for value in allow_from.split(",") if value.strip()}
        return owner_id in allowed
