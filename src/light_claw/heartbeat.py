from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Mapping, Optional

from .store import StateStore
from .task_executor import TaskExecutor


log = logging.getLogger("light_claw.heartbeat")


class WorkspaceHeartbeatService:
    def __init__(
        self,
        store: StateStore,
        executors: Mapping[str, TaskExecutor],
        interval_seconds: int,
        on_tick_success: Optional[Callable[[], None]] = None,
        on_tick_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self.store = store
        self.executors = dict(executors)
        self.interval_seconds = interval_seconds
        self.on_tick_success = on_tick_success
        self.on_tick_error = on_tick_error
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self.last_success_at: float | None = None
        self.last_error: str | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        await self.run_once()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> None:
        try:
            await self._run_due_tasks()
        except Exception as exc:
            self.last_error = str(exc)
            if self.on_tick_error is not None:
                self.on_tick_error(exc)
            raise
        self.last_success_at = time.time()
        self.last_error = None
        if self.on_tick_success is not None:
            self.on_tick_success()

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                try:
                    await self.run_once()
                except Exception:
                    log.exception("workspace heartbeat scan failed")

    async def _run_due_tasks(self) -> None:
        due_tasks = self.store.list_due_workspace_tasks(time.time())
        for task in due_tasks:
            executor = self.executors.get(task.agent_id)
            if executor is None:
                continue
            try:
                await executor.execute_workspace_task(
                    task,
                    trigger_source="heartbeat",
                    reschedule_seconds=self.interval_seconds,
                    announce_start=False,
                    deliver_result=True,
                )
            except Exception:
                log.exception("workspace task execution failed: %s", task.task_id)
