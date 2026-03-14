from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

from ..models import WorkspaceRecord


_SNAPSHOT_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "session-observations",
    "scheduled-tasks",
}
_SNAPSHOT_IGNORED_FILES = {".DS_Store"}
_OBSERVATION_MAX_FILES = 6
_OBSERVATION_MAX_FILE_BYTES = 24 * 1024
_OBSERVATION_MAX_TOTAL_CHARS = 48 * 1024
_OBSERVATION_MAX_ITEMS = 20


def record_observation(
    *,
    workspace: WorkspaceRecord,
    agent_id: str,
    conversation_id: str | None,
    conversation_owner_id: str | None,
    kind: str,
    text: str,
    context_key: str | None = None,
) -> bool:
    if not conversation_id or not conversation_owner_id:
        return False
    cleaned = text.strip()
    if not cleaned:
        return False
    path = _observation_queue_path(
        workspace=workspace,
        agent_id=agent_id,
        conversation_id=conversation_id,
        conversation_owner_id=conversation_owner_id,
    )
    entries = _load_observation_entries_from_path(path)
    normalized_context = context_key.strip().lower() if context_key else None
    if entries:
        last = entries[-1]
        if (
            last.get("text") == cleaned
            and last.get("context_key") == normalized_context
            and last.get("kind") == kind
        ):
            return False
    entries.append(
        {
            "kind": kind,
            "text": cleaned,
            "created_at": time.time(),
            "context_key": normalized_context,
        }
    )
    _write_observation_entries(path, entries[-_OBSERVATION_MAX_ITEMS:])
    return True


def clear_observations(
    *,
    workspace: WorkspaceRecord,
    agent_id: str,
    conversation_id: str | None,
    conversation_owner_id: str | None,
) -> None:
    if not conversation_id or not conversation_owner_id:
        return
    for path in (
        _workspace_snapshot_path(
            workspace=workspace,
            agent_id=agent_id,
            conversation_id=conversation_id,
            conversation_owner_id=conversation_owner_id,
        ),
        _observation_queue_path(
            workspace=workspace,
            agent_id=agent_id,
            conversation_id=conversation_id,
            conversation_owner_id=conversation_owner_id,
        ),
    ):
        try:
            path.unlink()
        except OSError:
            pass


def clear_workspace_observations(*, workspace: WorkspaceRecord) -> None:
    state_dir = _observation_state_dir(workspace)
    if not state_dir.exists():
        return
    for path in state_dir.iterdir():
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            continue


def drain_observation_entries(
    *,
    workspace: WorkspaceRecord,
    agent_id: str,
    conversation_id: str,
    conversation_owner_id: str,
) -> list[dict[str, object]]:
    path = _observation_queue_path(
        workspace=workspace,
        agent_id=agent_id,
        conversation_id=conversation_id,
        conversation_owner_id=conversation_owner_id,
    )
    entries = _load_observation_entries_from_path(path)
    if entries:
        try:
            path.unlink()
        except OSError:
            pass
    return entries


def format_observation_entry(entry: dict[str, object]) -> str:
    text = str(entry.get("text") or "").strip()
    if not text:
        return ""
    kind = str(entry.get("kind") or "observation").strip().lower() or "observation"
    created_at = entry.get("created_at")
    if isinstance(created_at, (int, float)):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(created_at))
    else:
        timestamp = "unknown-time"
    return "[{}] {}\n{}".format(timestamp, kind, text)


def build_workspace_observation_entry(
    *,
    workspace: WorkspaceRecord,
    session_id: str | None,
    snapshot_json: str | None,
) -> dict[str, object] | None:
    if not session_id or not snapshot_json:
        return None
    previous = _parse_workspace_snapshot(snapshot_json)
    current = _snapshot_workspace(workspace.path)
    added = sorted(path for path in current if path not in previous)
    modified = sorted(
        path for path, state in current.items() if previous.get(path) != state
    )
    modified = [path for path in modified if path not in added]
    deleted = sorted(path for path in previous if path not in current)
    observation = _render_workspace_observation(
        workspace.path,
        added=added,
        modified=modified,
        deleted=deleted,
    )
    if not observation:
        return None
    return {
        "kind": "workspace_change",
        "text": observation,
        "created_at": time.time(),
        "context_key": None,
    }


def load_workspace_snapshot(
    *,
    workspace: WorkspaceRecord,
    agent_id: str,
    conversation_id: str,
    conversation_owner_id: str,
) -> str | None:
    path = _workspace_snapshot_path(
        workspace=workspace,
        agent_id=agent_id,
        conversation_id=conversation_id,
        conversation_owner_id=conversation_owner_id,
    )
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def save_workspace_snapshot(
    *,
    workspace: WorkspaceRecord,
    agent_id: str,
    conversation_id: str,
    conversation_owner_id: str,
) -> None:
    path = _workspace_snapshot_path(
        workspace=workspace,
        agent_id=agent_id,
        conversation_id=conversation_id,
        conversation_owner_id=conversation_owner_id,
    )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_capture_workspace_snapshot(workspace.path), encoding="utf-8")
    except OSError:
        return


def _capture_workspace_snapshot(workspace_dir: Path) -> str:
    return json.dumps(
        _snapshot_workspace(workspace_dir),
        sort_keys=True,
        separators=(",", ":"),
    )


def _workspace_snapshot_path(
    *,
    workspace: WorkspaceRecord,
    agent_id: str,
    conversation_id: str,
    conversation_owner_id: str,
) -> Path:
    digest = _observation_digest(
        agent_id=agent_id,
        conversation_id=conversation_id,
        conversation_owner_id=conversation_owner_id,
        workspace_id=workspace.workspace_id,
    )
    return _observation_state_dir(workspace) / f"{digest}.snapshot.json"


def _observation_queue_path(
    *,
    workspace: WorkspaceRecord,
    agent_id: str,
    conversation_id: str,
    conversation_owner_id: str,
) -> Path:
    digest = _observation_digest(
        agent_id=agent_id,
        conversation_id=conversation_id,
        conversation_owner_id=conversation_owner_id,
        workspace_id=workspace.workspace_id,
    )
    return _observation_state_dir(workspace) / f"{digest}.queue.jsonl"


def _observation_digest(
    *,
    agent_id: str,
    conversation_id: str,
    conversation_owner_id: str,
    workspace_id: str,
) -> str:
    return hashlib.sha1(
        "{}:{}:{}:{}".format(
            agent_id,
            conversation_owner_id,
            conversation_id,
            workspace_id,
        ).encode("utf-8")
    ).hexdigest()


def _observation_state_dir(workspace: WorkspaceRecord) -> Path:
    return workspace.path / ".light-claw" / "session-observations"


def _load_observation_entries_from_path(path: Path) -> list[dict[str, object]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entries: list[dict[str, object]] = []
    for line in lines:
        raw = line.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        entries.append(entry)
    return entries


def _write_observation_entries(path: Path, entries: list[dict[str, object]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = "\n".join(
            json.dumps(entry, ensure_ascii=True, sort_keys=True) for entry in entries
        )
        path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
    except OSError:
        return


def _parse_workspace_snapshot(snapshot_json: str) -> dict[str, list[int]]:
    try:
        raw = json.loads(snapshot_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    snapshot: dict[str, list[int]] = {}
    for key, value in raw.items():
        if (
            isinstance(key, str)
            and isinstance(value, list)
            and len(value) == 2
            and all(isinstance(item, int) for item in value)
        ):
            snapshot[key] = [value[0], value[1]]
    return snapshot


def _snapshot_workspace(workspace_dir: Path) -> dict[str, list[int]]:
    if not workspace_dir.exists():
        return {}
    snapshot: dict[str, list[int]] = {}
    for root, dirnames, filenames in os.walk(workspace_dir):
        dirnames[:] = [name for name in dirnames if name not in _SNAPSHOT_IGNORED_DIRS]
        base = Path(root)
        for filename in sorted(filenames):
            if filename in _SNAPSHOT_IGNORED_FILES:
                continue
            path = base / filename
            try:
                stat = path.stat()
            except OSError:
                continue
            relative_path = path.relative_to(workspace_dir).as_posix()
            snapshot[relative_path] = [int(stat.st_mtime_ns), int(stat.st_size)]
    return snapshot


def _render_workspace_observation(
    workspace_dir: Path,
    *,
    added: list[str],
    modified: list[str],
    deleted: list[str],
) -> str:
    if not added and not modified and not deleted:
        return ""
    remaining_chars = _OBSERVATION_MAX_TOTAL_CHARS
    sections: list[str] = []
    included_paths = 0
    for label, paths in (("Added", added), ("Modified", modified)):
        for relative_path in paths:
            if included_paths >= _OBSERVATION_MAX_FILES or remaining_chars <= 0:
                break
            entry = _render_workspace_file_observation(
                workspace_dir / relative_path,
                label=label,
                relative_path=relative_path,
            )
            if not entry:
                continue
            if len(entry) > remaining_chars:
                entry = entry[:remaining_chars].rstrip() + "\n(truncated)\n"
            sections.append(entry)
            remaining_chars -= len(entry)
            included_paths += 1
    remaining_hidden = max(0, len(added) + len(modified) - included_paths)
    if deleted:
        deleted_block = "\n".join(
            ["Deleted files:"] + [f"- {relative_path}" for relative_path in deleted]
        )
        if len(deleted_block) <= remaining_chars:
            sections.append(deleted_block)
            remaining_chars -= len(deleted_block)
        else:
            remaining_hidden += len(deleted)
    if remaining_hidden > 0 and remaining_chars > 0:
        sections.append(
            f"{remaining_hidden} additional changed file(s) omitted from observation."
        )
    return "\n\n".join(section for section in sections if section).strip()


def _render_workspace_file_observation(
    path: Path,
    *,
    label: str,
    relative_path: str,
) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        return f"{label}: {relative_path}\nUnable to read file: {exc}"
    if b"\x00" in data:
        return (
            f"{label}: {relative_path}\n"
            f"Binary file observed. Size: {len(data)} bytes."
        )
    truncated = len(data) > _OBSERVATION_MAX_FILE_BYTES
    if truncated:
        data = data[:_OBSERVATION_MAX_FILE_BYTES]
    content = data.decode("utf-8", errors="replace")
    lines = [
        f"{label}: {relative_path}",
        "```text",
        content,
    ]
    if truncated:
        lines.append("... (truncated)")
    lines.append("```")
    return "\n".join(lines)
