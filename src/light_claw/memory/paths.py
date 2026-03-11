from __future__ import annotations

from datetime import date, datetime


def global_memory_relative_path() -> str:
    """Return the workspace-relative path for durable memory."""
    return "memory/CLAUDE.md"


def daily_memory_relative_path(entry_date: date | datetime | None = None) -> str:
    """Return the workspace-relative path for a dated daily memory note.

    Args:
        entry_date: Date used to name the daily memory file. Defaults to today.
    """
    if entry_date is None:
        entry_date = date.today()
    if isinstance(entry_date, datetime):
        entry_date = entry_date.date()
    return "memory/daily/{}.md".format(entry_date.isoformat())
