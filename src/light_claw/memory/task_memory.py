from __future__ import annotations

import time
from pathlib import Path

from ..models import TASK_STATUS_SUCCEEDED, WorkspaceRecord, WorkspaceTaskRecord
from .migration import legacy_project_memory, legacy_task_progress_note
from .storage import task_memory_relative_path as build_task_memory_relative_path

_TASK_MEMORY_MAX_CHARS = 2000


def record_task_memory_update(
    *,
    workspace: WorkspaceRecord,
    task: WorkspaceTaskRecord,
    result_status: str,
    result_answer: str,
    result_error: str | None,
    trigger_source: str,
) -> bool:
    """Append one task result summary to the task-scoped memory file.

    Args:
        workspace: Workspace that owns the task memory file.
        task: Task whose task memory should be updated.
        result_status: Final status for this task run.
        result_answer: Successful task answer text.
        result_error: Error text for failed task runs.
        trigger_source: Trigger label such as `heartbeat` or `cron`.
    """
    summary = _truncate_excerpt(
        result_answer if result_status == TASK_STATUS_SUCCEEDED else (result_error or ""),
        max_chars=_TASK_MEMORY_MAX_CHARS,
    ).strip()
    previous_summary = (
        task.last_result_excerpt if result_status == TASK_STATUS_SUCCEEDED else task.last_error_message
    ) or ""
    if not summary or summary == previous_summary.strip():
        return False
    path = task_memory_path(workspace, task)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    entry = "\n".join(
        [
            f"## {timestamp} [{trigger_source}] {result_status}",
            "",
            summary,
            "",
        ]
    ).strip()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text(encoding="utf-8").strip():
            existing = path.read_text(encoding="utf-8").rstrip()
            body = existing + "\n\n" + entry + "\n"
        else:
            body = _build_initial_task_memory(workspace, task, entry)
        path.write_text(body, encoding="utf-8")
    except OSError:
        return False
    return True


def task_memory_relative_path(task: WorkspaceTaskRecord | str) -> str:
    """Return the relative path for task-scoped memory.

    Args:
        task: Task record or raw task id.
    """
    task_id = task if isinstance(task, str) else task.task_id
    return build_task_memory_relative_path(task_id)


def task_memory_path(workspace: WorkspaceRecord, task: WorkspaceTaskRecord | str) -> Path:
    """Return the absolute task memory file path.

    Args:
        workspace: Workspace that owns the task memory file.
        task: Task record or raw task id.
    """
    return workspace.path / task_memory_relative_path(task)


def _build_initial_task_memory(
    workspace: WorkspaceRecord,
    task: WorkspaceTaskRecord,
    first_entry: str,
) -> str:
    sections = [
        "# Task Memory",
        "",
        f"- Task: {task.title} ({task.task_id})",
        "- Prompt:",
        "```text",
        task.prompt.strip(),
        "```",
        "",
    ]
    migrated_projects = legacy_project_memory(workspace.path)
    if migrated_projects:
        sections.extend(
            [
                "## Migrated Project Context",
                "",
                migrated_projects,
                "",
            ]
        )
    legacy_note = legacy_task_progress_note(workspace.path, task.task_id)
    if legacy_note:
        sections.extend(
            [
                "## Previous Task Notes",
                "",
                legacy_note,
                "",
            ]
        )
    sections.extend([first_entry, ""])
    return "\n".join(sections)


def _truncate_excerpt(answer: str, max_chars: int = 400) -> str:
    if len(answer) <= max_chars:
        return answer
    return answer[:max_chars].rstrip() + "..."
