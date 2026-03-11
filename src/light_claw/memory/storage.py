from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

_GLOBAL_MEMORY_FILE = "AGENTS.md"
_DAILY_MEMORY_DIR = Path("memory") / "daily"
_MEMORY_ROOT = Path("memory")
_LEGACY_MEMORY_FILES = {
    "memory/identity.md",
    "memory/profile.md",
    "memory/preferences.md",
    "memory/projects.md",
    "memory/decisions.md",
    "memory/open_loops.md",
    "memory/daily/README.md",
}


@dataclass(frozen=True)
class MemorySource:
    path: str
    scope: str


@dataclass(frozen=True)
class MemorySearchHit:
    path: str
    scope: str
    line_number: int
    preview: str


@dataclass(frozen=True)
class MemoryFileRead:
    path: str
    scope: str
    content: str
    start_line: int
    end_line: int


def global_memory_relative_path() -> str:
    """Return the relative path for durable global memory."""
    return _GLOBAL_MEMORY_FILE


def daily_memory_relative_path(entry_date: date | datetime | str | None = None) -> str:
    """Return the relative path for one dated daily memory note.

    Args:
        entry_date: Date value to use for the daily note path. When omitted, use today.
    """
    resolved_date = _resolve_entry_date(entry_date)
    return str(_DAILY_MEMORY_DIR / f"{resolved_date.isoformat()}.md")


def task_memory_relative_path(task_id: str) -> str:
    """Return the relative path for task-scoped memory.

    Args:
        task_id: Task id whose task memory file should be addressed.
    """
    return f"memory/{task_id}.md"


def classify_memory_scope(relative_path: str) -> str:
    """Classify a managed memory path into the exposed memory scope.

    Args:
        relative_path: Workspace-relative memory path.
    """
    normalized = relative_path.replace("\\", "/").strip()
    if normalized == _GLOBAL_MEMORY_FILE:
        return "global"
    if normalized.startswith("memory/daily/"):
        return "memory"
    return "group"


def list_memory_sources(workspace_dir: Path) -> list[MemorySource]:
    """List the managed memory files that belong to the workspace.

    Args:
        workspace_dir: Workspace root directory.
    """
    sources: list[MemorySource] = []
    global_path = workspace_dir / global_memory_relative_path()
    if global_path.exists():
        sources.append(
            MemorySource(
                path=global_memory_relative_path(),
                scope=classify_memory_scope(global_memory_relative_path()),
            )
        )
    memory_dir = workspace_dir / _MEMORY_ROOT
    if not memory_dir.exists():
        return sources
    for path in sorted(memory_dir.rglob("*.md")):
        relative_path = path.relative_to(workspace_dir).as_posix()
        if relative_path in _LEGACY_MEMORY_FILES:
            continue
        if relative_path.startswith("memory/tasks/"):
            continue
        sources.append(
            MemorySource(
                path=relative_path,
                scope=classify_memory_scope(relative_path),
            )
        )
    return sources


def search_memory_sources(
    workspace_dir: Path,
    query: str,
    *,
    limit: int = 20,
) -> list[MemorySearchHit]:
    """Search managed memory files for matching lines.

    Args:
        workspace_dir: Workspace root directory.
        query: Search text to match case-insensitively.
        limit: Maximum number of hits to return.
    """
    needle = query.strip().lower()
    if not needle or limit <= 0:
        return []
    hits: list[MemorySearchHit] = []
    for source in list_memory_sources(workspace_dir):
        path = workspace_dir / source.path
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for index, line in enumerate(lines, start=1):
            if needle not in line.lower():
                continue
            hits.append(
                MemorySearchHit(
                    path=source.path,
                    scope=source.scope,
                    line_number=index,
                    preview=line.strip() or "(blank line)",
                )
            )
            if len(hits) >= limit:
                return hits
    return hits


def read_memory_file(
    workspace_dir: Path,
    relative_path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
) -> MemoryFileRead:
    """Read one managed memory file or line range.

    Args:
        workspace_dir: Workspace root directory.
        relative_path: Workspace-relative memory path to read.
        start_line: Optional 1-based starting line number.
        end_line: Optional 1-based ending line number.
    """
    path = resolve_memory_path(workspace_dir, relative_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    start = start_line or 1
    if start < 1:
        raise ValueError("start_line must be >= 1")
    end = end_line if end_line is not None else max(len(lines), start)
    if end < start:
        raise ValueError("end_line must be >= start_line")
    selected = lines[start - 1 : end]
    actual_end = min(end, len(lines)) if lines else start - 1
    return MemoryFileRead(
        path=path.relative_to(workspace_dir).as_posix(),
        scope=classify_memory_scope(path.relative_to(workspace_dir).as_posix()),
        content="\n".join(selected),
        start_line=start,
        end_line=actual_end,
    )


def write_memory_file(workspace_dir: Path, relative_path: str, content: str) -> str:
    """Overwrite one managed memory file.

    Args:
        workspace_dir: Workspace root directory.
        relative_path: Workspace-relative memory path to update.
        content: Full markdown content to write.
    """
    path = resolve_memory_path(workspace_dir, relative_path, writable=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = content if content.endswith("\n") else content + "\n"
    path.write_text(normalized, encoding="utf-8")
    return path.relative_to(workspace_dir).as_posix()


def append_daily_memory_note(
    workspace_dir: Path,
    content: str,
    *,
    entry_date: date | datetime | str | None = None,
) -> str:
    """Append one dated note to the workspace daily memory file.

    Args:
        workspace_dir: Workspace root directory.
        content: Markdown content to append.
        entry_date: Optional date override for the daily note file.
    """
    relative_path = daily_memory_relative_path(entry_date)
    path = resolve_memory_path(workspace_dir, relative_path, writable=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    entry = "\n".join([f"## {timestamp}", "", content.strip()]).strip() + "\n"
    if path.exists() and path.read_text(encoding="utf-8").strip():
        existing = path.read_text(encoding="utf-8").rstrip()
        body = existing + "\n\n" + entry
    else:
        body = "# Daily Memory\n\n" + entry
    path.write_text(body, encoding="utf-8")
    return relative_path


def resolve_memory_path(
    workspace_dir: Path,
    relative_path: str,
    *,
    writable: bool = False,
) -> Path:
    """Resolve one managed memory path and reject paths outside the memory contract.

    Args:
        workspace_dir: Workspace root directory.
        relative_path: Workspace-relative memory path.
        writable: Whether the caller intends to create missing parents for writes.
    """
    normalized = relative_path.replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("memory path is required")
    candidate = (workspace_dir / normalized).resolve()
    workspace_root = workspace_dir.resolve()
    if candidate != workspace_root and workspace_root not in candidate.parents:
        raise ValueError("memory path escapes the workspace")
    if normalized == _GLOBAL_MEMORY_FILE:
        return candidate
    memory_root = (workspace_root / _MEMORY_ROOT).resolve()
    if memory_root not in candidate.parents:
        raise ValueError("memory path must live under memory/")
    if candidate.suffix.lower() != ".md":
        raise ValueError("memory path must point to a markdown file")
    if not writable and not candidate.exists():
        raise FileNotFoundError(normalized)
    return candidate


def _resolve_entry_date(entry_date: date | datetime | str | None) -> date:
    if entry_date is None:
        return date.today()
    if isinstance(entry_date, datetime):
        return entry_date.date()
    if isinstance(entry_date, date):
        return entry_date
    return date.fromisoformat(entry_date)
