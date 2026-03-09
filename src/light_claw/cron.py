from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Optional

from croniter import croniter

from .models import (
    SCHEDULE_KIND_CRON,
    SCHEDULE_KIND_INTERVAL,
    TASK_STATUS_SUCCEEDED,
    ScheduledTaskRecord,
    WorkspaceRecord,
)
from .store import StateStore
from .task_executor import TaskExecutor


log = logging.getLogger("light_claw.cron")
_DEFAULT_NO_CHANGE_LIMIT = 3


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
        no_change_limit: int = _DEFAULT_NO_CHANGE_LIMIT,
        on_tick_success: Optional[Callable[[], None]] = None,
        on_tick_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self.store = store
        self.executors = dict(executors)
        self.poll_interval_seconds = poll_interval_seconds
        self.no_change_limit = max(1, no_change_limit)
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
        workspace = self.store.get_workspace(
            schedule.agent_id,
            schedule.owner_id,
            schedule.workspace_id,
        )
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
        if workspace is None:
            self.store.update_scheduled_task_run(
                schedule.agent_id,
                schedule.owner_id,
                schedule.workspace_id,
                schedule.schedule_id,
                next_run_at=next_run_at,
                last_run_at=now,
                last_error_message="Workspace not found.",
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
        stop_message = None
        if result is not None:
            stop_message = self._update_no_change_state(
                workspace=workspace,
                schedule=schedule,
                result=result,
            )
        if stop_message is not None:
            self.store.update_scheduled_task_run(
                schedule.agent_id,
                schedule.owner_id,
                schedule.workspace_id,
                schedule.schedule_id,
                next_run_at=None,
                last_run_at=now,
                last_error_message=stop_message,
                enabled=False,
            )
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

    def _update_no_change_state(
        self,
        *,
        workspace: WorkspaceRecord,
        schedule: ScheduledTaskRecord,
        result,
    ) -> str | None:
        excerpt = self._result_excerpt(result)
        path = self._schedule_state_path(workspace, schedule.schedule_id)
        state = self._load_schedule_state(path)
        previous_excerpt = str(state.get("last_result_excerpt") or "").strip()
        if result.status == TASK_STATUS_SUCCEEDED and excerpt:
            streak = 1 if excerpt != previous_excerpt else int(state.get("streak") or 1) + 1
        else:
            streak = 0
        self._write_schedule_state(
            path,
            {
                "last_result_excerpt": excerpt,
                "streak": streak,
                "updated_at": time.time(),
            },
        )
        if streak >= self.no_change_limit:
            return "Stopped after {} consecutive no-change runs.".format(
                self.no_change_limit
            )
        return None

    @staticmethod
    def _result_excerpt(result) -> str:
        raw = result.answer if result.status == TASK_STATUS_SUCCEEDED else (result.error or "")
        text = str(raw).strip()
        if len(text) <= 400:
            return text
        return text[:400].rstrip() + "..."

    @staticmethod
    def _schedule_state_path(workspace: WorkspaceRecord, schedule_id: str) -> Path:
        return workspace.path / ".light-claw" / "scheduled-tasks" / f"{schedule_id}.json"

    @staticmethod
    def _load_schedule_state(path: Path) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write_schedule_state(path: Path, state: dict[str, object]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(state, ensure_ascii=True, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError:
            return
