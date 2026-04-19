from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .communication.base import BaseCommunicationChannel
from .communication.messages import ReplyTarget
from .config import AgentSettings, Settings
from .models import WorkspaceRecord
from .runtime import CliRuntimeError, CliRuntimeRegistry
from .store import StateStore

log = logging.getLogger("light_claw.task_executor")


@dataclass
class _ActivityTracker:
    last_activity_at: float

    def touch(self) -> None:
        self.last_activity_at = asyncio.get_running_loop().time()


@dataclass(frozen=True)
class TaskExecutionResult:
    status: str
    answer: str
    session_id: str | None
    error: str | None = None


class TaskExecutor:
    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings,
        store: StateStore,
        cli_registry: CliRuntimeRegistry,
        communication_channel: BaseCommunicationChannel,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.cli_registry = cli_registry
        self.communication_channel = communication_channel

    async def execute_prompt(
        self,
        *,
        workspace: WorkspaceRecord,
        prompt: str,
        conversation_id: str | None = None,
        conversation_owner_id: str | None = None,
        reply_target: ReplyTarget | None = None,
        announce_start: bool = True,
        deliver_result: bool = True,
    ) -> TaskExecutionResult:
        session_id = None
        if conversation_id and conversation_owner_id:
            session_id = self.store.get_workspace_session_id(
                self.agent.agent_id,
                conversation_id,
                conversation_owner_id,
                workspace.workspace_id,
            )
        if reply_target is not None and announce_start:
            await self.communication_channel.send_text(
                reply_target,
                "Agent {} ({}) is working in {} ({}) with {}...".format(
                    self.agent.name,
                    self.agent.agent_id,
                    workspace.name,
                    workspace.workspace_id,
                    workspace.cli_provider,
                ),
            )
        tracker = _ActivityTracker(asyncio.get_running_loop().time())
        heartbeat_task: asyncio.Task[None] | None = None
        if reply_target is not None and self.settings.status_heartbeat_enabled:
            heartbeat_task = asyncio.create_task(
                self._send_heartbeat(reply_target, workspace, tracker)
            )
        try:
            runtime = self.cli_registry.get_runtime(workspace.cli_provider)
            result = await runtime.run(
                prompt=prompt,
                workspace_dir=workspace.path,
                session_id=session_id,
                on_activity=tracker.touch,
            )
        except CliRuntimeError as exc:
            await self._stop_heartbeat(heartbeat_task)
            if reply_target is not None:
                await self.communication_channel.send_text(
                    reply_target,
                    "CLI run failed:\n{}".format(exc),
                )
            return TaskExecutionResult(
                status="failed",
                answer="",
                session_id=session_id,
                error=str(exc),
            )
        except Exception:
            log.exception("unexpected error during CLI run")
            await self._stop_heartbeat(heartbeat_task)
            if reply_target is not None:
                await self.communication_channel.send_text(
                    reply_target,
                    "CLI run failed:\nUnexpected internal error.",
                )
            return TaskExecutionResult(
                status="failed",
                answer="",
                session_id=session_id,
                error="Unexpected internal error.",
            )

        await self._stop_heartbeat(heartbeat_task)
        new_session_id = result.session_id or session_id
        if new_session_id and conversation_id and conversation_owner_id:
            self.store.set_session_id(
                self.agent.agent_id,
                conversation_id,
                conversation_owner_id,
                workspace.workspace_id,
                new_session_id,
            )
        if reply_target is not None and deliver_result:
            await self.communication_channel.send_text(reply_target, result.answer)
        return TaskExecutionResult(
            status="succeeded",
            answer=result.answer,
            session_id=new_session_id,
        )

    async def _send_heartbeat(
        self,
        reply_target: ReplyTarget,
        workspace: WorkspaceRecord,
        tracker: _ActivityTracker,
    ) -> None:
        started_at = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(self.settings.status_heartbeat_seconds)
            now = asyncio.get_running_loop().time()
            elapsed = int(now - started_at)
            idle = int(now - tracker.last_activity_at)
            await self.communication_channel.send_text(
                reply_target,
                "Agent {} is still running in {} ({}). Elapsed: {}s. Recent activity: {}s ago.".format(
                    self.agent.agent_id,
                    workspace.name,
                    workspace.workspace_id,
                    elapsed,
                    idle,
                ),
            )

    @staticmethod
    async def _stop_heartbeat(task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
