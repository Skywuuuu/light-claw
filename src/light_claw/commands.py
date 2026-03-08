from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Command:
    kind: str
    argument: Optional[str] = None


def parse_command(content: str) -> Optional[Command]:
    raw = content.strip()
    if not raw.startswith("/"):
        return None

    parts = raw.split()
    cmd = parts[0].lower()

    if cmd == "/help":
        return Command(kind="help")
    if cmd == "/reset":
        return Command(kind="reset")
    if cmd == "/cli":
        sub = parts[1].lower() if len(parts) > 1 else "current"
        if sub in {"list", "ls"}:
            return Command(kind="cli_list")
        if sub in {"current", "show"}:
            return Command(kind="cli_current")
        if sub in {"use", "switch"}:
            target = parts[2].strip() if len(parts) > 2 else ""
            return Command(kind="cli_use", argument=target or None)
        return Command(kind="invalid", argument=raw)
    if cmd == "/workspace":
        sub = parts[1].lower() if len(parts) > 1 else "current"
        if sub in {"list", "ls"}:
            return Command(kind="workspace_list")
        if sub in {"current", "show"}:
            return Command(kind="workspace_current")
        if sub in {"create", "new"}:
            name = " ".join(parts[2:]).strip()
            return Command(kind="workspace_create", argument=name or None)
        if sub in {"use", "switch"}:
            target = parts[2].strip() if len(parts) > 2 else ""
            return Command(kind="workspace_use", argument=target or None)
        return Command(kind="invalid", argument=raw)
    return None


def help_text() -> str:
    return "\n".join(
        [
            "Available commands:",
            "/help",
            "/cli list",
            "/cli current",
            "/cli use <provider>",
            "/workspace list",
            "/workspace create <name>",
            "/workspace use <id|index>",
            "/workspace current",
            "/reset",
        ]
    )
