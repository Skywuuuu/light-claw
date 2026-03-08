from __future__ import annotations

import asyncio
import logging
import shutil
import time
from pathlib import Path
from typing import Callable, Optional, Set

from .store import StateStore
from .workspaces import workspace_relative_dir


log = logging.getLogger("light_claw.archive")


class WorkspaceArchiveService:
    """Mirror workspace contents into an external archive directory."""

    def __init__(
        self,
        store: StateStore,
        archive_root: Path,
        interval_seconds: int,
        inbound_message_ttl_seconds: int = 0,
        on_sync_success: Optional[Callable[[], None]] = None,
        on_sync_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self.store = store
        self.archive_root = archive_root.resolve()
        self.interval_seconds = interval_seconds
        self.inbound_message_ttl_seconds = inbound_message_ttl_seconds
        self.on_sync_success = on_sync_success
        self.on_sync_error = on_sync_error
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self.last_success_at: Optional[float] = None
        self.last_error: Optional[str] = None

    async def start(self) -> None:
        """Start the background archive loop and run an initial sync."""

        if self._task is not None:
            return
        self.archive_root.mkdir(parents=True, exist_ok=True)
        await self.run_once()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background archive loop."""

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
        """Synchronize all known workspaces to the archive directory."""

        try:
            await asyncio.to_thread(self._sync_all_workspaces)
        except Exception as exc:
            self.last_error = str(exc)
            if self.on_sync_error is not None:
                self.on_sync_error(exc)
            raise
        self.last_success_at = time.time()
        self.last_error = None
        if self.on_sync_success is not None:
            self.on_sync_success()

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
                    log.exception("workspace archive sync failed")

    def _sync_all_workspaces(self) -> None:
        archive_workspaces_dir = self.archive_root / "workspaces"
        archive_workspaces_dir.mkdir(parents=True, exist_ok=True)
        seen_paths: Set[Path] = set()

        for workspace in self.store.list_all_workspaces():
            source_dir = workspace.path.resolve()
            relative_dir = workspace_relative_dir(
                workspace.agent_id,
                workspace.owner_id,
                workspace.workspace_id,
            )
            target_dir = archive_workspaces_dir / relative_dir
            seen_paths.add(relative_dir)

            if not source_dir.exists():
                log.warning("skip missing workspace during archive sync: %s", source_dir)
                continue

            _replace_directory(source_dir, target_dir)

        if self.inbound_message_ttl_seconds > 0:
            self.store.prune_inbound_messages(self.inbound_message_ttl_seconds)

        _prune_missing_workspaces(archive_workspaces_dir, seen_paths)


def _replace_directory(source_dir: Path, target_dir: Path) -> None:
    """Replace one archived workspace with a fresh copy from the source."""

    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=False)


def _prune_missing_workspaces(
    archive_workspaces_dir: Path,
    active_relative_dirs: Set[Path],
) -> None:
    """Remove archived workspaces that no longer exist in the state store."""

    if not archive_workspaces_dir.exists():
        return

    for workspace_dir in archive_workspaces_dir.rglob("*"):
        if not workspace_dir.is_dir():
            continue
        relative_dir = workspace_dir.relative_to(archive_workspaces_dir)
        if relative_dir in active_relative_dirs:
            continue
        if any(
            candidate == relative_dir
            or candidate in relative_dir.parents
            or relative_dir in candidate.parents
            for candidate in active_relative_dirs
        ):
            continue
        shutil.rmtree(workspace_dir)

    for directory in sorted(
        [path for path in archive_workspaces_dir.rglob("*") if path.is_dir()],
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if not any(directory.iterdir()):
            directory.rmdir()
