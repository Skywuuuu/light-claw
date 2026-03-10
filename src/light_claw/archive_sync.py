from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Set

from .store import StateStore
from .workspaces import workspace_relative_dir


log = logging.getLogger("light_claw.archive_sync")


def sync_all_workspaces(
    *,
    store: StateStore,
    archive_root: Path,
    inbound_message_ttl_seconds: int,
) -> None:
    archive_workspaces_dir = archive_root / "workspaces"
    archive_workspaces_dir.mkdir(parents=True, exist_ok=True)
    seen_paths: Set[Path] = set()

    for workspace in store.list_all_workspaces():
        source_dir = workspace.path.resolve()
        relative_dir = workspace_relative_dir(workspace.agent_id)
        target_dir = archive_workspaces_dir / relative_dir
        seen_paths.add(relative_dir)

        if not source_dir.exists():
            log.warning("skip missing workspace during archive sync: %s", source_dir)
            continue

        _replace_directory(source_dir, target_dir)

    if inbound_message_ttl_seconds > 0:
        store.prune_inbound_messages(inbound_message_ttl_seconds)

    _prune_missing_workspaces(archive_workspaces_dir, seen_paths)


def _replace_directory(source_dir: Path, target_dir: Path) -> None:
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir, dirs_exist_ok=False)


def _prune_missing_workspaces(
    archive_workspaces_dir: Path,
    active_relative_dirs: Set[Path],
) -> None:
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
