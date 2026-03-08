from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from .cli_runners import CliRunnerError, CliRunnerRegistry
from .config import AgentSettings, Settings
from .feishu import FeishuClient
from .models import (
    TASK_STATUS_FAILED,
    TASK_STATUS_RUNNING,
    TASK_STATUS_SUCCEEDED,
    CliRunResult,
    FeishuReplyTarget,
    TaskRunRecord,
    WorkspaceRecord,
    WorkspaceTaskRecord,
)
from .store import StateStore


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
        cli_registry: CliRunnerRegistry,
        feishu_client: FeishuClient,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.cli_registry = cli_registry
        self.feishu_client = feishu_client

    async def execute_prompt(
        self,
        *,
        workspace: WorkspaceRecord,
        prompt: str,
        conversation_id: str | None = None,
        conversation_owner_id: str | None = None,
        reply_target: FeishuReplyTarget | None = None,
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
            await self.feishu_client.send_text(
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
        if reply_target is not None:
            heartbeat_task = asyncio.create_task(
                self._send_heartbeat(reply_target, workspace, tracker)
            )
        try:
            runner = self.cli_registry.get_runner(workspace.cli_provider)
            result = await runner.run(
                prompt=prompt,
                workspace_dir=workspace.path,
                session_id=session_id,
                on_activity=tracker.touch,
            )
        except CliRunnerError as exc:
            await self._stop_heartbeat(heartbeat_task)
            error = str(exc)
            if reply_target is not None:
                await self.feishu_client.send_text(
                    reply_target,
                    "CLI run failed:\n{}".format(error),
                )
            return TaskExecutionResult(
                status=TASK_STATUS_FAILED,
                answer="",
                session_id=session_id,
                error=error,
            )
        except Exception:
            await self._stop_heartbeat(heartbeat_task)
            error = "Unexpected internal error."
            if reply_target is not None:
                await self.feishu_client.send_text(
                    reply_target,
                    "CLI run failed:\n{}".format(error),
                )
            return TaskExecutionResult(
                status=TASK_STATUS_FAILED,
                answer="",
                session_id=session_id,
                error=error,
            )

        await self._stop_heartbeat(heartbeat_task)
        self._persist_session(
            workspace=workspace,
            cli_result=result,
            conversation_id=conversation_id,
            conversation_owner_id=conversation_owner_id,
        )
        if reply_target is not None and deliver_result:
            await self.feishu_client.send_text(reply_target, result.answer)
        return TaskExecutionResult(
            status=TASK_STATUS_SUCCEEDED,
            answer=result.answer,
            session_id=result.session_id or session_id,
        )

    async def execute_workspace_task(
        self,
        task: WorkspaceTaskRecord,
        *,
        trigger_source: str,
        reschedule_seconds: int | None = None,
        announce_start: bool = False,
        deliver_result: bool = True,
    ) -> TaskExecutionResult | None:
        run = self.store.claim_workspace_task(
            task.agent_id,
            task.owner_id,
            task.workspace_id,
            task.task_id,
            trigger_source=trigger_source,
            conversation_id=task.notify_conversation_id,
            conversation_owner_id=task.notify_owner_id,
        )
        if run is None:
            return None
        workspace = self.store.get_workspace(task.agent_id, task.owner_id, task.workspace_id)
        if workspace is None:
            self.store.complete_task_run(
                task.agent_id,
                run.run_id,
                status=TASK_STATUS_FAILED,
                task_status=TASK_STATUS_FAILED,
                error_message="Workspace not found.",
                result_excerpt=None,
                next_run_at=None,
            )
            return TaskExecutionResult(
                status=TASK_STATUS_FAILED,
                answer="",
                session_id=None,
                error="Workspace not found.",
            )

        reply_target = self._task_reply_target(task)
        result = await self.execute_prompt(
            workspace=workspace,
            prompt=task.prompt,
            conversation_id=task.notify_conversation_id,
            conversation_owner_id=task.notify_owner_id,
            reply_target=reply_target,
            announce_start=announce_start,
            deliver_result=deliver_result,
        )
        next_run_at = None
        task_status = result.status
        if result.status == TASK_STATUS_SUCCEEDED and reschedule_seconds:
            next_run_at = time.time() + reschedule_seconds
            task_status = TASK_STATUS_RUNNING
        self.store.complete_task_run(
            task.agent_id,
            run.run_id,
            status=result.status,
            task_status=task_status,
            error_message=result.error,
            result_excerpt=self._truncate_excerpt(result.answer),
            next_run_at=next_run_at,
        )
        return result

    async def _send_heartbeat(
        self,
        reply_target: FeishuReplyTarget,
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

    def _persist_session(
        self,
        *,
        workspace: WorkspaceRecord,
        cli_result: CliRunResult,
        conversation_id: str | None,
        conversation_owner_id: str | None,
    ) -> None:
        if (
            not cli_result.session_id
            or not conversation_id
            or not conversation_owner_id
        ):
            return
        self.store.set_session_id(
            self.agent.agent_id,
            conversation_id,
            conversation_owner_id,
            workspace.workspace_id,
            cli_result.session_id,
        )

    @staticmethod
    def _truncate_excerpt(answer: str, max_chars: int = 400) -> str:
        if len(answer) <= max_chars:
            return answer
        return answer[:max_chars].rstrip() + "..."

    @staticmethod
    def _task_reply_target(task: WorkspaceTaskRecord) -> FeishuReplyTarget | None:
        if not task.notify_receive_id or not task.notify_receive_id_type:
            return None
        return FeishuReplyTarget(
            receive_id=task.notify_receive_id,
            receive_id_type=task.notify_receive_id_type,
        )
