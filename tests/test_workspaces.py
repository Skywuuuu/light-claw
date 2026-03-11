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
                name="Platform Agent",
                cli_provider="codex",
                agent_name="Writer",
            )
            manager.ensure_workspace_layout(workspace, agent_name="Writer")

            self.assertTrue((workspace.path / "AGENTS.md").exists())
            self.assertTrue((workspace.path / ".light-claw" / "agent.json").exists())
            self.assertTrue((workspace.path / "memory").is_dir())
            self.assertTrue((workspace.path / "memory" / "identity.md").exists())
            self.assertTrue((workspace.path / "memory" / "daily").is_dir())
            self.assertEqual(
                workspace_relative_dir("writer"),
                Path("writer"),
            )
            self.assertEqual(workspace.workspace_id, "default")

    def test_workspace_path_is_agent_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            manager = WorkspaceManager(Path(tmp_dir))
            workspace = manager.create_workspace(
                "writer",
                "Agent",
                cli_provider="codex",
                agent_name="Writer",
            )
            self.assertEqual(workspace.path, Path(tmp_dir).resolve() / "writer")


if __name__ == "__main__":
    unittest.main()
