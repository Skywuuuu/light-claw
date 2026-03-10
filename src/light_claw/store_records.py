from __future__ import annotations

import sqlite3
from pathlib import Path

from .models import (
    ScheduledTaskRecord,
    TaskRunRecord,
    WorkspaceRecord,
    WorkspaceTaskRecord,
)


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


def row_to_workspace_task(row: sqlite3.Row) -> WorkspaceTaskRecord:
    return WorkspaceTaskRecord(
        agent_id=str(row["agent_id"]),
        owner_id=str(row["owner_id"]),
        workspace_id=str(row["workspace_id"]),
        task_id=str(row["task_id"]),
        title=str(row["title"]),
        prompt=str(row["prompt"]),
        status=str(row["status"]),
        notify_conversation_id=(
            str(row["notify_conversation_id"])
            if row["notify_conversation_id"]
            else None
        ),
        notify_owner_id=str(row["notify_owner_id"]) if row["notify_owner_id"] else None,
        notify_receive_id=(
            str(row["notify_receive_id"]) if row["notify_receive_id"] else None
        ),
        notify_receive_id_type=(
            str(row["notify_receive_id_type"])
            if row["notify_receive_id_type"]
            else None
        ),
        last_run_at=float(row["last_run_at"]) if row["last_run_at"] else None,
        next_run_at=float(row["next_run_at"]) if row["next_run_at"] else None,
        last_error_message=(
            str(row["last_error_message"]) if row["last_error_message"] else None
        ),
        last_result_excerpt=(
            str(row["last_result_excerpt"]) if row["last_result_excerpt"] else None
        ),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def row_to_scheduled_task(row: sqlite3.Row) -> ScheduledTaskRecord:
    return ScheduledTaskRecord(
        agent_id=str(row["agent_id"]),
        owner_id=str(row["owner_id"]),
        workspace_id=str(row["workspace_id"]),
        schedule_id=str(row["schedule_id"]),
        task_id=str(row["task_id"]),
        kind=str(row["kind"]),
        interval_seconds=(
            int(row["interval_seconds"])
            if row["interval_seconds"] is not None
            else None
        ),
        cron_expr=str(row["cron_expr"]) if row["cron_expr"] else None,
        enabled=bool(row["enabled"]),
        next_run_at=float(row["next_run_at"]) if row["next_run_at"] else None,
        last_run_at=float(row["last_run_at"]) if row["last_run_at"] else None,
        last_error_message=(
            str(row["last_error_message"]) if row["last_error_message"] else None
        ),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def row_to_task_run(row: sqlite3.Row) -> TaskRunRecord:
    return TaskRunRecord(
        agent_id=str(row["agent_id"]),
        owner_id=str(row["owner_id"]),
        workspace_id=str(row["workspace_id"]),
        task_id=str(row["task_id"]),
        run_id=str(row["run_id"]),
        trigger_source=str(row["trigger_source"]),
        status=str(row["status"]),
        conversation_id=str(row["conversation_id"]) if row["conversation_id"] else None,
        conversation_owner_id=(
            str(row["conversation_owner_id"]) if row["conversation_owner_id"] else None
        ),
        started_at=float(row["started_at"]),
        finished_at=float(row["finished_at"]) if row["finished_at"] else None,
        error_message=str(row["error_message"]) if row["error_message"] else None,
        result_excerpt=str(row["result_excerpt"]) if row["result_excerpt"] else None,
    )
