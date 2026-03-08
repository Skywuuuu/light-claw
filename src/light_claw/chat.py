from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Protocol

from .cli_runners import CliRunnerError, CliRunnerRegistry
from .commands import Command, help_text, parse_command
from .config import AgentSettings, Settings
from .feishu import FeishuClient
from .models import FeishuInboundMessage, WorkspaceRecord
from .store import StateStore
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


@dataclass
class _ActivityTracker:
    last_activity_at: float

    def touch(self) -> None:
        self.last_activity_at = asyncio.get_running_loop().time()


class ChatService:
    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings,
        store: StateStore,
        workspace_manager: WorkspaceManager,
        cli_registry: CliRunnerRegistry,
        feishu_client: FeishuClient,
        observer: ChatObserver | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.workspace_manager = workspace_manager
        self.cli_registry = cli_registry
        self.feishu_client = feishu_client
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
        if command.kind == "reset":
            self.store.clear_session(
                self.agent.agent_id,
                message.conversation_id,
                message.owner_id,
            )
            return "Current workspace session cleared. The next message will start a new session."
        if command.kind == "cli_list":
            workspace = self._ensure_current_workspace(
                owner_id=message.owner_id,
                conversation_id=message.conversation_id,
            )
            return self._render_cli_list(workspace.cli_provider)
        if command.kind == "cli_current":
            workspace = self._ensure_current_workspace(
                owner_id=message.owner_id,
                conversation_id=message.conversation_id,
            )
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
            workspace = self._ensure_current_workspace(
                owner_id=message.owner_id,
                conversation_id=message.conversation_id,
            )
            ok, reason = self.cli_registry.validate_selectable(command.argument)
            if not ok:
                return reason
            updated = self.store.set_workspace_cli_provider(
                self.agent.agent_id,
                message.owner_id,
                workspace.workspace_id,
                command.argument.strip().lower(),
            )
            if updated is None:
                return "Failed to update workspace CLI provider."
            self._ensure_workspace_layout(updated)
            return "\n".join(
                [
                    "CLI provider updated.",
                    "{} now uses `{}`.".format(updated.name, updated.cli_provider),
                ]
            )
        if command.kind == "workspace_list":
            workspace = self._ensure_current_workspace(
                owner_id=message.owner_id,
                conversation_id=message.conversation_id,
            )
            workspaces = self.store.list_workspaces(self.agent.agent_id, message.owner_id)
            return self._render_workspace_list(workspaces, workspace.workspace_id)
        if command.kind == "workspace_current":
            workspace = self._ensure_current_workspace(
                owner_id=message.owner_id,
                conversation_id=message.conversation_id,
            )
            state = self.store.get_conversation_state(
                self.agent.agent_id,
                message.conversation_id,
                message.owner_id,
            )
            return "\n".join(
                [
                    "Current workspace:",
                    "Agent: {} ({})".format(self.agent.name, self.agent.agent_id),
                    "{} ({})".format(workspace.name, workspace.workspace_id),
                    "CLI provider: {}".format(workspace.cli_provider),
                    str(workspace.path),
                    "Current session: {}".format(state.session_id or "(none)"),
                ]
            )
        if command.kind == "workspace_create":
            if not command.argument:
                return "Usage: /workspace create <name>"
            workspaces = self.store.list_workspaces(self.agent.agent_id, message.owner_id)
            record = self.workspace_manager.create_workspace(
                agent_id=self.agent.agent_id,
                owner_id=message.owner_id,
                name=command.argument,
                existing_ids=[workspace.workspace_id for workspace in workspaces],
                cli_provider=self.cli_registry.default_provider_id(
                    self.agent.default_cli_provider
                ),
                agent_name=self.agent.name,
                skills_path=self.agent.skills_path,
                mcp_config_path=self.agent.mcp_config_path,
            )
            record = self.store.create_workspace(record)
            self._ensure_workspace_layout(record)
            self.store.set_current_workspace(
                self.agent.agent_id,
                message.conversation_id,
                message.owner_id,
                record.workspace_id,
            )
            return "\n".join(
                [
                    "Workspace created and selected.",
                    "Agent: {} ({})".format(self.agent.name, self.agent.agent_id),
                    "{} ({})".format(record.name, record.workspace_id),
                    "CLI provider: {}".format(record.cli_provider),
                    str(record.path),
                ]
            )
        if command.kind == "workspace_use":
            if not command.argument:
                return "Usage: /workspace use <id|index>"
            workspaces = self.store.list_workspaces(self.agent.agent_id, message.owner_id)
            target = self._resolve_workspace_target(workspaces, command.argument)
            if not target:
                return "Workspace not found. Use `/workspace list` first."
            self.store.set_current_workspace(
                self.agent.agent_id,
                message.conversation_id,
                message.owner_id,
                target.workspace_id,
            )
            return "\n".join(
                [
                    "Workspace selected.",
                    "Agent: {} ({})".format(self.agent.name, self.agent.agent_id),
                    "{} ({})".format(target.name, target.workspace_id),
                    "CLI provider: {}".format(target.cli_provider),
                    str(target.path),
                ]
            )
        if command.kind == "invalid":
            return "Unknown workspace command. Use `/help`."
        return None

    async def _handle_prompt(self, message: FeishuInboundMessage) -> str:
        workspace = self._ensure_current_workspace(
            owner_id=message.owner_id,
            conversation_id=message.conversation_id,
        )
        state = self.store.get_conversation_state(
            self.agent.agent_id,
            message.conversation_id,
            message.owner_id,
        )
        await self.feishu_client.send_text(
            message.reply_target,
            "Agent {} ({}) is working in {} ({}) with {}...".format(
                self.agent.name,
                self.agent.agent_id,
                workspace.name,
                workspace.workspace_id,
                workspace.cli_provider,
            ),
        )
        tracker = _ActivityTracker(asyncio.get_running_loop().time())
        heartbeat_task = asyncio.create_task(
            self._send_heartbeat(message, workspace, tracker)
        )
        try:
            runner = self.cli_registry.get_runner(workspace.cli_provider)
            result = await runner.run(
                prompt=message.content,
                workspace_dir=workspace.path,
                session_id=state.session_id if state else None,
                on_activity=tracker.touch,
            )
        except CliRunnerError as exc:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            await self.feishu_client.send_text(
                message.reply_target,
                "CLI run failed:\n{}".format(str(exc)),
            )
            return "cli_failed"
        except Exception:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
            await self.feishu_client.send_text(
                message.reply_target,
                "CLI run failed:\nUnexpected internal error.",
            )
            return "cli_failed"
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
        if result.session_id:
            self.store.set_session_id(
                self.agent.agent_id,
                message.conversation_id,
                message.owner_id,
                workspace.workspace_id,
                result.session_id,
            )
        await self.feishu_client.send_text(message.reply_target, result.answer)
        return "prompt"

    async def _send_heartbeat(
        self,
        message: FeishuInboundMessage,
        workspace: WorkspaceRecord,
        tracker: _ActivityTracker,
    ) -> None:
        started_at = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(self.settings.status_heartbeat_seconds)
            now = asyncio.get_running_loop().time()
            elapsed = int(now - started_at)
            idle = int(now - tracker.last_activity_at)
            await self.feishu_client.send_text(
                message.reply_target,
                "Agent {} is still running in {} ({}). Elapsed: {}s. Recent activity: {}s ago.".format(
                    self.agent.agent_id,
                    workspace.name,
                    workspace.workspace_id,
                    elapsed,
                    idle,
                ),
            )

    def _ensure_current_workspace(
        self, owner_id: str, conversation_id: str
    ) -> WorkspaceRecord:
        state = self.store.get_conversation_state(
            self.agent.agent_id,
            conversation_id,
            owner_id,
        )
        if state and state.workspace_id:
            workspace = self.store.get_workspace(
                self.agent.agent_id,
                owner_id,
                state.workspace_id,
            )
            if workspace:
                self._ensure_workspace_layout(workspace)
                return workspace

        workspaces = self.store.list_workspaces(self.agent.agent_id, owner_id)
        if workspaces:
            self.store.set_current_workspace(
                self.agent.agent_id,
                conversation_id,
                owner_id,
                workspaces[0].workspace_id,
            )
            self._ensure_workspace_layout(workspaces[0])
            return workspaces[0]

        created = self.workspace_manager.create_workspace(
            agent_id=self.agent.agent_id,
            owner_id=owner_id,
            name=self.agent.default_workspace_name,
            existing_ids=[],
            cli_provider=self.cli_registry.default_provider_id(
                self.agent.default_cli_provider
            ),
            agent_name=self.agent.name,
            skills_path=self.agent.skills_path,
            mcp_config_path=self.agent.mcp_config_path,
        )
        created = self.store.create_workspace(created)
        self._ensure_workspace_layout(created)
        self.store.set_current_workspace(
            self.agent.agent_id,
            conversation_id,
            owner_id,
            created.workspace_id,
        )
        return created

    def _ensure_workspace_layout(self, workspace: WorkspaceRecord) -> None:
        self.workspace_manager.ensure_workspace_layout(
            workspace,
            agent_name=self.agent.name,
            skills_path=self.agent.skills_path,
            mcp_config_path=self.agent.mcp_config_path,
        )

    def _resolve_workspace_target(
        self, workspaces: Iterable[WorkspaceRecord], target: str
    ) -> Optional[WorkspaceRecord]:
        items = list(workspaces)
        raw = target.strip()
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(items):
                return items[index - 1]
        for workspace in items:
            if workspace.workspace_id == raw:
                return workspace
        return None

    def _render_workspace_list(
        self, workspaces: Iterable[WorkspaceRecord], current_workspace_id: str
    ) -> str:
        lines = ["Workspaces:"]
        for index, workspace in enumerate(workspaces, start=1):
            marker = "->" if workspace.workspace_id == current_workspace_id else "  "
            lines.append(
                "{} {}. {} ({}) [{}]".format(
                    marker,
                    index,
                    workspace.name,
                    workspace.workspace_id,
                    workspace.cli_provider,
                )
            )
            lines.append(str(workspace.path))
        lines.append("Use `/workspace use <id|index>` to switch.")
        return "\n".join(lines)

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
        lines.append("Use `/cli use <provider>` to switch the current workspace.")
        return "\n".join(lines)

    def _is_allowed_user(self, owner_id: str) -> bool:
        allow_from = self.agent.allow_from.strip()
        if not allow_from or allow_from == "*":
            return True
        allowed = {value.strip() for value in allow_from.split(",") if value.strip()}
        return owner_id in allowed
