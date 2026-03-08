from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Callable, Mapping, Optional

from croniter import croniter

from .models import SCHEDULE_KIND_CRON, SCHEDULE_KIND_INTERVAL, ScheduledTaskRecord
from .store import StateStore
from .task_executor import TaskExecutor


log = logging.getLogger("light_claw.cron")


def compute_next_run_at(schedule: ScheduledTaskRecord, now: float) -> float | None:
    if schedule.kind == SCHEDULE_KIND_INTERVAL:
        if not schedule.interval_seconds or schedule.interval_seconds <= 0:
            return None
        return now + schedule.interval_seconds
    if schedule.kind == SCHEDULE_KIND_CRON:
        if not schedule.cron_expr:
            return None
        return float(croniter(schedule.cron_expr, datetime.fromtimestamp(now)).get_next())
    return None


class CronService:
    def __init__(
        self,
        store: StateStore,
        executors: Mapping[str, TaskExecutor],
        poll_interval_seconds: int,
        on_tick_success: Optional[Callable[[], None]] = None,
        on_tick_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self.store = store
        self.executors = dict(executors)
        self.poll_interval_seconds = poll_interval_seconds
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
            await self._run_due_schedules()
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
                    timeout=self.poll_interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                try:
                    await self.run_once()
                except Exception:
                    log.exception("scheduled task scan failed")

    async def _run_due_schedules(self) -> None:
        due_schedules = self.store.list_due_scheduled_tasks(time.time())
        for schedule in due_schedules:
            await self._execute_schedule(schedule)

    async def _execute_schedule(self, schedule: ScheduledTaskRecord) -> None:
        now = time.time()
        next_run_at = compute_next_run_at(schedule, now)
        task = self.store.get_workspace_task(
            schedule.agent_id,
            schedule.owner_id,
            schedule.workspace_id,
            schedule.task_id,
        )
        executor = self.executors.get(schedule.agent_id)
        if executor is None:
            self.store.update_scheduled_task_run(
                schedule.agent_id,
                schedule.owner_id,
                schedule.workspace_id,
                schedule.schedule_id,
                next_run_at=next_run_at,
                last_run_at=now,
                last_error_message="Agent executor not found.",
            )
            return
        if task is None:
            self.store.update_scheduled_task_run(
                schedule.agent_id,
                schedule.owner_id,
                schedule.workspace_id,
                schedule.schedule_id,
                next_run_at=next_run_at,
                last_run_at=now,
                last_error_message="Task not found.",
            )
            return
        try:
            result = await executor.execute_workspace_task(
                task,
                trigger_source="cron",
                reschedule_seconds=None,
                announce_start=False,
                deliver_result=True,
            )
        except Exception as exc:
            self.store.update_scheduled_task_run(
                schedule.agent_id,
                schedule.owner_id,
                schedule.workspace_id,
                schedule.schedule_id,
                next_run_at=next_run_at,
                last_run_at=now,
                last_error_message=str(exc),
            )
            log.exception("scheduled task failed: %s", schedule.schedule_id)
            return
        self.store.update_scheduled_task_run(
            schedule.agent_id,
            schedule.owner_id,
            schedule.workspace_id,
            schedule.schedule_id,
            next_run_at=next_run_at,
            last_run_at=now,
            last_error_message=result.error if result is not None else None,
        )
