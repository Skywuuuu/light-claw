import tempfile
import unittest
from pathlib import Path

from light_claw.workspaces import WorkspaceManager, workspace_relative_dir


class WorkspaceManagerTest(unittest.TestCase):
    def test_creates_bootstrapped_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = WorkspaceManager(Path(tmp_dir))
            workspace = manager.create_workspace(
                agent_id="writer",
                owner_id="ou_1",
                name="Platform Agent",
                existing_ids=[],
                cli_provider="codex",
                agent_name="Writer",
            )
            manager.ensure_workspace_layout(workspace, agent_name="Writer")

            self.assertTrue((workspace.path / "AGENTS.md").exists())
            self.assertTrue((workspace.path / ".light-claw" / "agent.json").exists())
            self.assertTrue((workspace.path / "memory").is_dir())
            self.assertTrue((workspace.path / "memory" / "identity.md").exists())
            self.assertTrue((workspace.path / "memory" / "daily" / "README.md").exists())
            self.assertEqual(
                workspace_relative_dir("writer", "ou_1", "platform-agent"),
                Path("writer") / "ou_1" / "platform-agent",
            )

    def test_workspace_ids_are_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = WorkspaceManager(Path(tmp_dir))
            first = manager.create_workspace(
                "writer",
                "ou_1",
                "Agent",
                existing_ids=[],
                cli_provider="codex",
                agent_name="Writer",
            )
            second = manager.create_workspace(
                "writer",
                "ou_1",
                "Agent",
                existing_ids=[first.workspace_id],
                cli_provider="codex",
                agent_name="Writer",
            )
            self.assertNotEqual(first.workspace_id, second.workspace_id)


if __name__ == "__main__":
    unittest.main()
