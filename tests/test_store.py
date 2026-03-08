import tempfile
import unittest
from pathlib import Path

from light_claw.models import SCHEDULE_KIND_INTERVAL, TASK_STATUS_CANCELLED, WorkspaceRecord
from light_claw.store import StateStore


class StoreTest(unittest.TestCase):
    def test_workspace_and_conversation_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            workspace = store.create_workspace(
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
            store.set_current_workspace(
                "agent-a",
                "conv_1",
                "ou_1",
                workspace.workspace_id,
            )
            store.set_session_id(
                "agent-a",
                "conv_1",
                "ou_1",
                workspace.workspace_id,
                "session_1",
            )

            current = store.get_conversation_state("agent-a", "conv_1", "ou_1")
            self.assertIsNotNone(current)
            self.assertEqual(current.agent_id, "agent-a")
            self.assertEqual(current.workspace_id, "default")
            self.assertEqual(current.session_id, "session_1")

            store.set_current_workspace(
                "agent-a",
                "conv_1",
                "ou_1",
                workspace.workspace_id,
            )
            resumed = store.get_conversation_state("agent-a", "conv_1", "ou_1")
            self.assertIsNotNone(resumed)
            self.assertEqual(resumed.session_id, "session_1")

            store.clear_session("agent-a", "conv_1", "ou_1")
            cleared = store.get_conversation_state("agent-a", "conv_1", "ou_1")
            self.assertIsNotNone(cleared)
            self.assertIsNone(cleared.session_id)
            store.close()

    def test_updates_workspace_cli_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            workspace = store.create_workspace(
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
            updated = store.set_workspace_cli_provider(
                workspace.agent_id,
                workspace.owner_id,
                workspace.workspace_id,
                "codex",
            )
            self.assertIsNotNone(updated)
            self.assertEqual(updated.cli_provider, "codex")
            store.close()

    def test_deduplicates_inbound_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            self.assertTrue(store.remember_inbound_message("agent-a", "msg_1"))
            self.assertFalse(store.remember_inbound_message("agent-a", "msg_1"))
            self.assertTrue(store.remember_inbound_message("agent-b", "msg_1"))
            store.close()

    def test_agent_scoped_sessions_do_not_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            for agent_id in ("agent-a", "agent-b"):
                store.create_workspace(
                    WorkspaceRecord(
                        agent_id=agent_id,
                        owner_id="ou_1",
                        workspace_id="default",
                        name="Default",
                        path=Path(tmp_dir) / agent_id / "default",
                        cli_provider="codex",
                        created_at=0.0,
                        updated_at=0.0,
                    )
                )
                store.set_session_id(
                    agent_id,
                    "conv_1",
                    "ou_1",
                    "default",
                    f"session-{agent_id}",
                )

            agent_a = store.get_conversation_state("agent-a", "conv_1", "ou_1")
            agent_b = store.get_conversation_state("agent-b", "conv_1", "ou_1")
            self.assertEqual(agent_a.session_id, "session-agent-a")
            self.assertEqual(agent_b.session_id, "session-agent-b")
            store.close()

    def test_workspace_tasks_and_runs_round_trip(self) -> None:
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
                "Check repository health",
                notify_conversation_id="conv_1",
                notify_owner_id="ou_1",
                notify_receive_id="ou_1",
                notify_receive_id_type="open_id",
            )
            self.assertEqual(len(store.list_workspace_tasks("agent-a", "ou_1", "default")), 1)

            run = store.claim_workspace_task(
                "agent-a",
                "ou_1",
                "default",
                task.task_id,
                trigger_source="heartbeat",
                conversation_id="conv_1",
                conversation_owner_id="ou_1",
            )
            self.assertIsNotNone(run)
            self.assertIsNone(
                store.claim_workspace_task(
                    "agent-a",
                    "ou_1",
                    "default",
                    task.task_id,
                    trigger_source="heartbeat",
                )
            )

            completed = store.complete_task_run(
                "agent-a",
                run.run_id,
                status="succeeded",
                task_status="running",
                result_excerpt="All good",
                next_run_at=123.0,
            )
            self.assertIsNotNone(completed)
            updated = store.get_workspace_task("agent-a", "ou_1", "default", task.task_id)
            self.assertEqual(updated.status, "running")
            self.assertEqual(updated.last_result_excerpt, "All good")
            self.assertEqual(updated.next_run_at, 123.0)
            store.close()

    def test_scheduled_tasks_can_be_created_listed_and_removed(self) -> None:
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
                "Nightly summary",
            )
            schedule = store.create_scheduled_task(
                "agent-a",
                "ou_1",
                "default",
                task.task_id,
                kind=SCHEDULE_KIND_INTERVAL,
                interval_seconds=60,
                next_run_at=1.0,
            )
            due = store.list_due_scheduled_tasks(10.0)
            self.assertEqual(len(due), 1)
            self.assertEqual(due[0].schedule_id, schedule.schedule_id)

            updated = store.update_workspace_task(
                "agent-a",
                "ou_1",
                "default",
                task.task_id,
                status=TASK_STATUS_CANCELLED,
                next_run_at=None,
            )
            self.assertEqual(updated.status, TASK_STATUS_CANCELLED)
            self.assertTrue(
                store.remove_scheduled_task(
                    "agent-a",
                    "ou_1",
                    "default",
                    schedule.schedule_id,
                )
            )
            self.assertEqual(
                len(store.list_scheduled_tasks("agent-a", "ou_1", "default")),
                0,
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
