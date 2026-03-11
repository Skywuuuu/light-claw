import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from light_claw.archive import (
    ARCHIVE_DAILY_TIME_SETTING_KEY,
    WorkspaceArchiveService,
    compute_next_daily_run_at,
)
from light_claw.models import WorkspaceRecord
from light_claw.store import StateStore


class WorkspaceArchiveServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._tmp_dir = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup_tmp_dir)
        self.base_dir = Path(self._tmp_dir.name)
        self.store = StateStore(self.base_dir / "light-claw.db")
        self.addAsyncCleanup(self._close_store)

    async def _cleanup_tmp_dir(self) -> None:
        self._tmp_dir.cleanup()

    async def _close_store(self) -> None:
        self.store.close()

    def _create_workspace(self, agent_id: str, owner_id: str, workspace_id: str) -> Path:
        workspace_dir = self.base_dir / "source" / agent_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")
        (workspace_dir / "memory").mkdir()
        (workspace_dir / "memory" / "daily").mkdir(parents=True, exist_ok=True)
        (workspace_dir / "memory" / "daily" / "2026-03-11.md").write_text(
            "# Daily Memory\n",
            encoding="utf-8",
        )
        self.store.create_workspace(
            WorkspaceRecord(
                agent_id=agent_id,
                owner_id=owner_id,
                workspace_id=workspace_id,
                name=workspace_id,
                path=workspace_dir,
                cli_provider="codex",
                created_at=0.0,
                updated_at=0.0,
            )
        )
        return workspace_dir

    async def test_run_once_copies_workspace_contents(self) -> None:
        self._create_workspace("writer", "ou_1", "default")
        archive_root = self.base_dir / "archive"
        service = WorkspaceArchiveService(
            store=self.store,
            archive_root=archive_root,
            interval_seconds=12 * 60 * 60,
        )

        await service.run_once()

        self.assertTrue(
            (
                archive_root
                / "workspaces"
                / "writer"
                / "AGENTS.md"
            ).exists()
        )
        self.assertTrue(
            (
                archive_root
                / "workspaces"
                / "writer"
                / "memory"
                / "daily"
                / "2026-03-11.md"
            ).exists()
        )

    async def test_run_once_prunes_removed_archived_workspaces(self) -> None:
        self._create_workspace("writer", "ou_1", "default")
        archive_root = self.base_dir / "archive"
        stale_dir = archive_root / "workspaces" / "writer" / "stale"
        stale_dir.mkdir(parents=True, exist_ok=True)
        (stale_dir / "README.md").write_text("stale\n", encoding="utf-8")
        service = WorkspaceArchiveService(
            store=self.store,
            archive_root=archive_root,
            interval_seconds=12 * 60 * 60,
        )

        await service.run_once()

        self.assertFalse(stale_dir.exists())

    async def test_start_runs_initial_sync_before_interval_wait(self) -> None:
        self._create_workspace("writer", "ou_1", "default")
        archive_root = self.base_dir / "archive"
        service = WorkspaceArchiveService(
            store=self.store,
            archive_root=archive_root,
            interval_seconds=12 * 60 * 60,
        )

        await service.start()
        self.addAsyncCleanup(service.stop)

        self.assertTrue(
            (
                archive_root
                / "workspaces"
                / "writer"
                / "AGENTS.md"
            ).exists()
        )

    def test_compute_next_daily_run_at_rolls_forward(self) -> None:
        now = datetime(2025, 1, 1, 10, 0, tzinfo=timezone.utc).timestamp()
        same_day = compute_next_daily_run_at(now, "23:30", timezone.utc)
        self.assertEqual(
            datetime.fromtimestamp(same_day, tz=timezone.utc),
            datetime(2025, 1, 1, 23, 30, tzinfo=timezone.utc),
        )

        next_day = compute_next_daily_run_at(same_day, "23:30", timezone.utc)
        self.assertEqual(
            datetime.fromtimestamp(next_day, tz=timezone.utc),
            datetime(2025, 1, 2, 23, 30, tzinfo=timezone.utc),
        )

    async def test_update_daily_time_persists_runtime_setting(self) -> None:
        service = WorkspaceArchiveService(
            store=self.store,
            archive_root=self.base_dir / "archive",
            interval_seconds=12 * 60 * 60,
        )

        updated = service.update_daily_time("3:15")

        self.assertEqual(updated, "03:15")
        self.assertEqual(service.daily_time, "03:15")
        self.assertIsNotNone(service.next_run_at)
        self.assertEqual(
            self.store.get_app_setting(ARCHIVE_DAILY_TIME_SETTING_KEY),
            "03:15",
        )
