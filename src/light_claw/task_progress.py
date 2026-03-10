from __future__ import annotations

import time
from pathlib import Path

from .models import TASK_STATUS_SUCCEEDED, WorkspaceRecord, WorkspaceTaskRecord


_TASK_PROGRESS_MAX_CHARS = 2000


def record_task_progress(
    *,
    workspace: WorkspaceRecord,
    task: WorkspaceTaskRecord,
    result_status: str,
    result_answer: str,
    result_error: str | None,
    trigger_source: str,
) -> bool:
    summary = _truncate_excerpt(
        result_answer if result_status == TASK_STATUS_SUCCEEDED else (result_error or ""),
        max_chars=_TASK_PROGRESS_MAX_CHARS,
    ).strip()
    previous_summary = (
        task.last_result_excerpt
        if result_status == TASK_STATUS_SUCCEEDED
        else task.last_error_message
    ) or ""
    if not summary or summary == previous_summary.strip():
        return False
    path = task_progress_path(workspace, task)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    entry_lines = [
        "## {} [{}] {}".format(timestamp, trigger_source, result_status),
        "",
        summary,
        "",
    ]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = path.read_text(encoding="utf-8").rstrip()
            body = "{}\n\n{}".format(existing, "\n".join(entry_lines)).strip() + "\n"
        else:
            body = "\n".join(
                [
                    "# Task Progress",
                    "",
                    "- Task: {} ({})".format(task.title, task.task_id),
                    "- Prompt:",
                    "```text",
                    task.prompt.strip(),
                    "```",
                    "",
                    "\n".join(entry_lines).strip(),
                    "",
                ]
            )
        path.write_text(body, encoding="utf-8")
    except OSError:
        return False
    return True


def task_progress_relative_path(task: WorkspaceTaskRecord) -> str:
    return "memory/tasks/{}.md".format(task.task_id)


def task_progress_path(
    workspace: WorkspaceRecord,
    task: WorkspaceTaskRecord,
) -> Path:
    return workspace.path / task_progress_relative_path(task)


def _truncate_excerpt(answer: str, max_chars: int = 400) -> str:
    if len(answer) <= max_chars:
        return answer
    return answer[:max_chars].rstrip() + "..."
