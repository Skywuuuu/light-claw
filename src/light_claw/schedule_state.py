from __future__ import annotations

import json
import time
from pathlib import Path

from .models import TASK_STATUS_SUCCEEDED, WorkspaceRecord


def update_no_change_state(
    *,
    workspace: WorkspaceRecord,
    schedule_id: str,
    result,
    no_change_limit: int,
) -> str | None:
    excerpt = _result_excerpt(result)
    path = _schedule_state_path(workspace, schedule_id)
    state = _load_schedule_state(path)
    previous_excerpt = str(state.get("last_result_excerpt") or "").strip()
    if result.status == TASK_STATUS_SUCCEEDED and excerpt:
        streak = 1 if excerpt != previous_excerpt else int(state.get("streak") or 1) + 1
    else:
        streak = 0
    _write_schedule_state(
        path,
        {
            "last_result_excerpt": excerpt,
            "streak": streak,
            "updated_at": time.time(),
        },
    )
    if streak >= no_change_limit:
        return "Stopped after {} consecutive no-change runs.".format(no_change_limit)
    return None


def _result_excerpt(result) -> str:
    raw = result.answer if result.status == TASK_STATUS_SUCCEEDED else (result.error or "")
    text = str(raw).strip()
    if len(text) <= 400:
        return text
    return text[:400].rstrip() + "..."


def _schedule_state_path(workspace: WorkspaceRecord, schedule_id: str) -> Path:
    return workspace.path / ".light-claw" / "scheduled-tasks" / f"{schedule_id}.json"


def _load_schedule_state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_schedule_state(path: Path, state: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(state, ensure_ascii=True, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return
