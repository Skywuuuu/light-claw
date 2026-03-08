import tempfile
import unittest
from pathlib import Path

from light_claw.cron import CronService, compute_next_run_at
from light_claw.models import SCHEDULE_KIND_INTERVAL, WorkspaceRecord
from light_claw.store import StateStore


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute_workspace_task(
        self,
        task,
        *,
        trigger_source,
        reschedule_seconds=None,
        announce_start=False,
        deliver_result=True,
    ):
        self.calls.append((task.task_id, trigger_source))
        return None


class CronServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_once_executes_due_schedules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=Path(tmp_dir) / "default",
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            task = store.create_workspace_task(
                "agent-a",
                "ou_1",
                "default",
                "Send status report",
            )
            schedule = store.create_scheduled_task(
                "agent-a",
                "ou_1",
                "default",
                task.task_id,
                kind=SCHEDULE_KIND_INTERVAL,
                interval_seconds=30,
                next_run_at=0.0,
            )
            executor = _FakeExecutor()
            service = CronService(
                store=store,
                executors={"agent-a": executor},
                poll_interval_seconds=30,
            )

            await service.run_once()

            self.assertEqual(executor.calls, [(task.task_id, "cron")])
            updated = store.list_scheduled_tasks("agent-a", "ou_1", "default")[0]
            self.assertEqual(updated.schedule_id, schedule.schedule_id)
            self.assertIsNotNone(updated.next_run_at)
            store.close()

    def test_compute_next_run_at_for_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=Path(tmp_dir) / "default",
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            task = store.create_workspace_task("agent-a", "ou_1", "default", "Do work")
            schedule = store.create_scheduled_task(
                "agent-a",
                "ou_1",
                "default",
                task.task_id,
                kind=SCHEDULE_KIND_INTERVAL,
                interval_seconds=45,
                next_run_at=1.0,
            )
            self.assertEqual(compute_next_run_at(schedule, 100.0), 145.0)
            store.close()
