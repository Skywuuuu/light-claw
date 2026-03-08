import tempfile
import unittest
from pathlib import Path

from light_claw.heartbeat import WorkspaceHeartbeatService
from light_claw.models import WorkspaceRecord
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
        self.calls.append((task.task_id, trigger_source, reschedule_seconds))
        return None


class HeartbeatServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_once_executes_due_workspace_tasks(self) -> None:
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
                "Check inbox",
                next_run_at=0.0,
            )
            executor = _FakeExecutor()
            service = WorkspaceHeartbeatService(
                store=store,
                executors={"agent-a": executor},
                interval_seconds=60,
            )

            await service.run_once()

            self.assertEqual(executor.calls, [(task.task_id, "heartbeat", 60)])
            store.close()
