from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import WorkspaceRecord


def row_to_workspace(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        agent_id=str(row["agent_id"]),
        owner_id=str(row["owner_id"]),
        workspace_id=str(row["workspace_id"]),
        name=str(row["name"]),
        path=Path(str(row["path"])),
        cli_provider=str(row["cli_provider"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )
