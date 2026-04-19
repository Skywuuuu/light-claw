import tempfile
import unittest
from pathlib import Path

from light_claw.models import WorkspaceRecord
from light_claw.store import StateStore


class StoreTest(unittest.TestCase):
    def test_agent_workspace_and_sessions(self) -> None:
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
            store.set_session_id(
                "agent-a",
                "conv_1",
                "ou_1",
                workspace.workspace_id,
                "session_1",
            )

            current = store.get_agent_workspace("agent-a")
            self.assertIsNotNone(current)
            self.assertEqual(current.agent_id, "agent-a")
            self.assertEqual(current.workspace_id, "default")
            self.assertEqual(
                store.get_workspace_session_id(
                    "agent-a",
                    "conv_1",
                    "ou_1",
                    workspace.workspace_id,
                ),
                "session_1",
            )

            store.clear_session("agent-a", "conv_1", "ou_1")
            self.assertIsNone(
                store.get_workspace_session_id(
                    "agent-a",
                    "conv_1",
                    "ou_1",
                    workspace.workspace_id,
                )
            )
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

            self.assertEqual(
                store.get_workspace_session_id("agent-a", "conv_1", "ou_1", "default"),
                "session-agent-a",
            )
            self.assertEqual(
                store.get_workspace_session_id("agent-b", "conv_1", "ou_1", "default"),
                "session-agent-b",
            )
            store.close()

    def test_clear_workspace_sessions_removes_all_sessions_for_workspace(self) -> None:
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
            store.set_session_id("agent-a", "conv_1", "ou_1", "default", "session-1")
            store.set_session_id("agent-a", "conv_2", "ou_2", "default", "session-2")

            store.clear_workspace_sessions("agent-a", "default")

            self.assertIsNone(
                store.get_workspace_session_id("agent-a", "conv_1", "ou_1", "default")
            )
            self.assertIsNone(
                store.get_workspace_session_id("agent-a", "conv_2", "ou_2", "default")
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
