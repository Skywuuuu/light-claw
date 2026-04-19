# Strip to Chat MVP — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strip light-claw to its minimal core: receive a Feishu message → run a CLI agent (Codex or Claude Code) → send the reply back. Delete everything else.

**Architecture:** Feishu message → `ChatService` → `TaskExecutor.execute_prompt()` → `CliRuntime.run()` → Feishu reply. Session persistence (for CLI resume) in SQLite. No background services, no task system, no memory management (agents handle their own memory natively). Remaining commands: `/help`, `/reset`, `/cli list|current|use`.

**Tech Stack:** Python 3.9+, FastAPI, httpx, lark-oapi, python-dotenv, uvicorn, SQLite

---

### Task 1: Delete standalone modules and their tests

These files are leaf nodes — nothing in the kept codebase imports them.

**Files:**
- Delete: `src/light_claw/archive.py`
- Delete: `src/light_claw/archive_sync.py`
- Delete: `src/light_claw/heartbeat.py`
- Delete: `src/light_claw/cron.py`
- Delete: `src/light_claw/schedule_state.py`
- Delete: `src/light_claw/task_commands.py`
- Delete: `tests/test_archive.py`
- Delete: `tests/test_heartbeat.py`
- Delete: `tests/test_cron.py`

- [ ] **Step 1: Delete the 6 source files**

```bash
rm src/light_claw/archive.py \
   src/light_claw/archive_sync.py \
   src/light_claw/heartbeat.py \
   src/light_claw/cron.py \
   src/light_claw/schedule_state.py \
   src/light_claw/task_commands.py
```

- [ ] **Step 2: Delete the 3 test files**

```bash
rm tests/test_archive.py \
   tests/test_heartbeat.py \
   tests/test_cron.py
```

- [ ] **Step 3: Commit**

```bash
git add -A
git commit -m "refactor: delete archive, heartbeat, cron, schedule_state, and task_commands"
```

---

### Task 2: Delete memory package and simplify task_executor

Claude Code and Codex both manage their own memory and context natively. The entire `memory/` package (prompt injection, session observations, workspace snapshots, task progress) is redundant. Deleting it requires simultaneously updating `task_executor.py` which imports from it.

**Files:**
- Delete: `src/light_claw/memory/__init__.py`
- Delete: `src/light_claw/memory/guidance.py`
- Delete: `src/light_claw/memory/session_observations.py`
- Delete: `src/light_claw/memory/task_progress.py`
- Modify: `src/light_claw/task_executor.py`

- [ ] **Step 1: Delete the entire memory package**

```bash
rm -r src/light_claw/memory
```

- [ ] **Step 2: Rewrite task_executor.py**

Strip all memory/observation/snapshot imports and code. Remove `execute_workspace_task()` and its helpers. Keep only `execute_prompt()` (the core: get session → run CLI → persist session → reply) and the status heartbeat (periodic "still working" pings — a communication feature).

```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .communication.base import BaseCommunicationChannel
from .communication.messages import ReplyTarget
from .config import AgentSettings, Settings
from .models import WorkspaceRecord
from .runtime import CliRuntimeError, CliRuntimeRegistry
from .store import StateStore

log = logging.getLogger("light_claw.task_executor")


@dataclass
class _ActivityTracker:
    last_activity_at: float

    def touch(self) -> None:
        self.last_activity_at = asyncio.get_running_loop().time()


@dataclass(frozen=True)
class TaskExecutionResult:
    status: str
    answer: str
    session_id: str | None
    error: str | None = None


class TaskExecutor:
    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings,
        store: StateStore,
        cli_registry: CliRuntimeRegistry,
        communication_channel: BaseCommunicationChannel,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.cli_registry = cli_registry
        self.communication_channel = communication_channel

    async def execute_prompt(
        self,
        *,
        workspace: WorkspaceRecord,
        prompt: str,
        conversation_id: str | None = None,
        conversation_owner_id: str | None = None,
        reply_target: ReplyTarget | None = None,
        announce_start: bool = True,
        deliver_result: bool = True,
    ) -> TaskExecutionResult:
        session_id = None
        if conversation_id and conversation_owner_id:
            session_id = self.store.get_workspace_session_id(
                self.agent.agent_id,
                conversation_id,
                conversation_owner_id,
                workspace.workspace_id,
            )
        if reply_target is not None and announce_start:
            await self.communication_channel.send_text(
                reply_target,
                "Agent {} ({}) is working in {} ({}) with {}...".format(
                    self.agent.name,
                    self.agent.agent_id,
                    workspace.name,
                    workspace.workspace_id,
                    workspace.cli_provider,
                ),
            )
        tracker = _ActivityTracker(asyncio.get_running_loop().time())
        heartbeat_task: asyncio.Task[None] | None = None
        if reply_target is not None and self.settings.status_heartbeat_enabled:
            heartbeat_task = asyncio.create_task(
                self._send_heartbeat(reply_target, workspace, tracker)
            )
        try:
            runtime = self.cli_registry.get_runtime(workspace.cli_provider)
            result = await runtime.run(
                prompt=prompt,
                workspace_dir=workspace.path,
                session_id=session_id,
                on_activity=tracker.touch,
            )
        except CliRuntimeError as exc:
            await self._stop_heartbeat(heartbeat_task)
            if reply_target is not None:
                await self.communication_channel.send_text(
                    reply_target,
                    "CLI run failed:\n{}".format(exc),
                )
            return TaskExecutionResult(
                status="failed",
                answer="",
                session_id=session_id,
                error=str(exc),
            )
        except Exception:
            log.exception("unexpected error during CLI run")
            await self._stop_heartbeat(heartbeat_task)
            if reply_target is not None:
                await self.communication_channel.send_text(
                    reply_target,
                    "CLI run failed:\nUnexpected internal error.",
                )
            return TaskExecutionResult(
                status="failed",
                answer="",
                session_id=session_id,
                error="Unexpected internal error.",
            )

        await self._stop_heartbeat(heartbeat_task)
        new_session_id = result.session_id or session_id
        if new_session_id and conversation_id and conversation_owner_id:
            self.store.set_session_id(
                self.agent.agent_id,
                conversation_id,
                conversation_owner_id,
                workspace.workspace_id,
                new_session_id,
            )
        if reply_target is not None and deliver_result:
            await self.communication_channel.send_text(reply_target, result.answer)
        return TaskExecutionResult(
            status="succeeded",
            answer=result.answer,
            session_id=new_session_id,
        )

    async def _send_heartbeat(
        self,
        reply_target: ReplyTarget,
        workspace: WorkspaceRecord,
        tracker: _ActivityTracker,
    ) -> None:
        started_at = asyncio.get_running_loop().time()
        while True:
            await asyncio.sleep(self.settings.status_heartbeat_seconds)
            now = asyncio.get_running_loop().time()
            elapsed = int(now - started_at)
            idle = int(now - tracker.last_activity_at)
            await self.communication_channel.send_text(
                reply_target,
                "Agent {} is still running in {} ({}). Elapsed: {}s. Recent activity: {}s ago.".format(
                    self.agent.agent_id,
                    workspace.name,
                    workspace.workspace_id,
                    elapsed,
                    idle,
                ),
            )

    @staticmethod
    async def _stop_heartbeat(task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
```

- [ ] **Step 3: Verify import**

```bash
uv run python -c "from light_claw.task_executor import TaskExecutor; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: delete memory package and strip task_executor to prompt execution only"
```

---

### Task 3: Simplify data layer — models, store_records, store

Remove task/schedule/run models, record mappers, and all associated tables and methods from the store. Also remove `app_setting` table (only consumer was archive).

**Files:**
- Modify: `src/light_claw/models.py`
- Modify: `src/light_claw/store_records.py`
- Modify: `src/light_claw/store.py`

- [ ] **Step 1: Rewrite models.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class WorkspaceRecord:
    agent_id: str
    owner_id: str
    workspace_id: str
    name: str
    path: Path
    cli_provider: str
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class CliRunResult:
    session_id: Optional[str]
    answer: str
    raw_output: str


@dataclass(frozen=True)
class CliProviderInfo:
    provider_id: str
    display_name: str
    description: str
    available: bool
```

- [ ] **Step 2: Rewrite store_records.py**

```python
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
```

- [ ] **Step 3: Simplify store.py**

Remove imports of all task/schedule types: `TASK_STATUS_FAILED`, `TASK_STATUS_RUNNING`, `ScheduledTaskRecord`, `TaskRunRecord`, `WorkspaceTaskRecord`, `row_to_scheduled_task`, `row_to_task_run`, `row_to_workspace_task`.

Remove column sets: `WORKSPACE_TASK_COLUMNS`, `SCHEDULED_TASK_COLUMNS`, `TASK_RUN_COLUMNS`, `APP_SETTING_COLUMNS`.

In `_create_tables()`, remove these CREATE TABLE/INDEX statements:
- `workspace_task` table
- `scheduled_task` table
- `task_run` table
- `app_setting` table
- `idx_workspace_task_lookup`
- `idx_workspace_task_next_run`
- `idx_scheduled_task_next_run`
- `idx_task_run_active`

Remove these methods entirely:
- `get_app_setting`
- `set_app_setting`
- `create_workspace_task`
- `list_workspace_tasks`
- `list_due_workspace_tasks`
- `get_workspace_task`
- `get_latest_task_run`
- `update_workspace_task`
- `create_scheduled_task`
- `list_scheduled_tasks`
- `list_due_scheduled_tasks`
- `remove_scheduled_task`
- `claim_workspace_task`
- `complete_task_run`
- `recover_orphaned_task_runs`
- `update_scheduled_task_run`

The imports become:

```python
from .models import WorkspaceRecord
from .store_records import row_to_workspace
```

- [ ] **Step 4: Verify**

```bash
uv run python -c "from light_claw.store import StateStore; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add src/light_claw/models.py src/light_claw/store_records.py src/light_claw/store.py
git commit -m "refactor: strip task/schedule/run data from models, store_records, and store"
```

---

### Task 4: Simplify config and commands

Remove archive/heartbeat/cron settings. Remove `/task`, `/cron`, `/archive` command parsing.

**Files:**
- Modify: `src/light_claw/config.py`
- Modify: `src/light_claw/commands.py`

- [ ] **Step 1: Remove settings fields from config.py**

Remove these fields from the `Settings` dataclass and all related code in `from_env()`, `ensure_directories()`, and `validate()`:
- `archive_enabled`, `archive_dir`, `archive_interval_seconds`
- `task_heartbeat_enabled`, `task_heartbeat_interval_seconds`
- `cron_enabled`, `cron_poll_interval_seconds`

In `from_env()`, remove:
- The `archive_dir` path resolution (`_resolve_optional_path` call for `LIGHT_CLAW_ARCHIVE_DIR`)
- The `archive_enabled`, `archive_dir`, `archive_interval_seconds` keyword args
- The `task_heartbeat_enabled`, `task_heartbeat_interval_seconds` keyword args
- The `cron_enabled`, `cron_poll_interval_seconds` keyword args

In `ensure_directories()`, remove:
- `if self.archive_enabled: self.archive_dir.mkdir(parents=True, exist_ok=True)`

In `validate()`, remove:
- `if self.archive_interval_seconds <= 0` check
- `if self.task_heartbeat_interval_seconds <= 0` check
- `if self.cron_poll_interval_seconds <= 0` check

- [ ] **Step 2: Rewrite commands.py**

```python
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
    return None


def help_text() -> str:
    return "\n".join(
        [
            "Available commands:",
            "/help",
            "/cli list",
            "/cli current",
            "/cli use <provider>",
            "/reset",
        ]
    )
```

- [ ] **Step 3: Verify**

```bash
uv run python -c "from light_claw.config import Settings; print('OK')"
uv run python -c "from light_claw.commands import parse_command, help_text; print('OK')"
```

- [ ] **Step 4: Commit**

```bash
git add src/light_claw/config.py src/light_claw/commands.py
git commit -m "refactor: remove archive/heartbeat/cron settings and task/cron/archive commands"
```

---

### Task 5: Simplify chat_commands, chat, and workspaces

Remove archive/task/cron handling from commands. Remove `archive_service` from chat. Remove memory file bootstrapping from workspaces (agents manage their own memory).

**Files:**
- Modify: `src/light_claw/chat_commands.py`
- Modify: `src/light_claw/chat.py`
- Modify: `src/light_claw/workspaces.py`

- [ ] **Step 1: Rewrite chat_commands.py**

After removing archive/task/cron and all observation recording, `ChatCommandHandler` no longer needs `task_executor` at all — the only calls were for observation recording/clearing.

```python
from __future__ import annotations

from typing import Optional

from .communication.base import BaseCommunicationChannel
from .communication.messages import InboundMessage
from .commands import Command, help_text
from .config import AgentSettings, Settings
from .models import WorkspaceRecord
from .runtime import CliRuntimeRegistry
from .store import StateStore
from .workspaces import WorkspaceManager


class ChatCommandHandler:
    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings,
        store: StateStore,
        workspace_manager: WorkspaceManager,
        cli_registry: CliRuntimeRegistry,
        communication_channel: BaseCommunicationChannel,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.workspace_manager = workspace_manager
        self.cli_registry = cli_registry
        self.communication_channel = communication_channel

    async def handle(
        self,
        message: InboundMessage,
        command: Command,
    ) -> Optional[str]:
        if command.kind == "help":
            return help_text()
        if command.kind == "reset":
            self.store.clear_session(
                self.agent.agent_id,
                message.conversation_id,
                message.owner_id,
            )
            return "Current workspace session cleared. The next message will start a new session."
        if command.kind == "cli_list":
            workspace = self.ensure_workspace()
            return self._render_cli_list(workspace.cli_provider)
        if command.kind == "cli_current":
            workspace = self.ensure_workspace()
            provider = self.cli_registry.get_provider(workspace.cli_provider)
            return "\n".join(
                [
                    "Current CLI provider:",
                    "{} ({})".format(
                        provider.display_name if provider else workspace.cli_provider,
                        workspace.cli_provider,
                    ),
                    "Workspace: {} ({})".format(workspace.name, workspace.workspace_id),
                ]
            )
        if command.kind == "cli_use":
            if not command.argument:
                return "Usage: /cli use <provider>"
            workspace = self.ensure_workspace()
            previous_provider = workspace.cli_provider
            ok, reason = self.cli_registry.validate_selectable(command.argument)
            if not ok:
                return reason
            updated = self.store.set_workspace_cli_provider(
                workspace.agent_id,
                workspace.owner_id,
                workspace.workspace_id,
                command.argument.strip().lower(),
            )
            if updated is None:
                return "Failed to update workspace CLI provider."
            self._ensure_workspace_layout(updated)
            session_reset = previous_provider != updated.cli_provider
            if session_reset:
                self.store.clear_workspace_sessions(
                    updated.agent_id,
                    updated.workspace_id,
                )
            return "\n".join(
                ["CLI provider updated.", "{} now uses `{}`.".format(updated.name, updated.cli_provider)]
                + (
                    [
                        "Existing workspace CLI sessions were cleared so the new provider starts fresh."
                    ]
                    if session_reset
                    else []
                )
            )
        if command.kind == "invalid":
            return "Unknown command. Use `/help`."
        return None

    def ensure_workspace(self) -> WorkspaceRecord:
        workspace = self.store.get_agent_workspace(self.agent.agent_id)
        if workspace is not None:
            self._ensure_workspace_layout(workspace)
            return workspace
        created = self.workspace_manager.create_workspace(
            agent_id=self.agent.agent_id,
            name=self.agent.default_workspace_name,
            cli_provider=self.cli_registry.default_provider_id(
                self.agent.default_cli_provider
            ),
            agent_name=self.agent.name,
            skills_path=self.agent.skills_path,
            mcp_config_path=self.agent.mcp_config_path,
        )
        created = self.store.create_workspace(created)
        self._ensure_workspace_layout(created)
        return created

    def get_workspace(self) -> WorkspaceRecord | None:
        return self.store.get_agent_workspace(self.agent.agent_id)

    def _ensure_workspace_layout(self, workspace: WorkspaceRecord) -> None:
        self.workspace_manager.ensure_workspace_layout(
            workspace,
            agent_name=self.agent.name,
            skills_path=self.agent.skills_path,
            mcp_config_path=self.agent.mcp_config_path,
        )

    def _render_cli_list(self, current_provider_id: str) -> str:
        lines = ["CLI providers:"]
        for provider in self.cli_registry.list_providers():
            marker = "->" if provider.provider_id == current_provider_id else "  "
            status = "ready" if provider.available else "reserved"
            lines.append(
                "{} {} ({}) [{}]".format(
                    marker,
                    provider.display_name,
                    provider.provider_id,
                    status,
                )
            )
            lines.append(provider.description)
        lines.append("Use `/cli use <provider>` to switch the current agent workspace.")
        return "\n".join(lines)
```

- [ ] **Step 2: Rewrite chat.py**

Remove `archive_service` parameter. Remove `task_executor` from `ChatCommandHandler` construction. `ChatService` still needs `task_executor` for `_handle_prompt()`.

```python
from __future__ import annotations

import asyncio
from typing import Dict, Protocol

from .chat_commands import ChatCommandHandler
from .communication.base import BaseCommunicationChannel
from .communication.messages import InboundMessage
from .commands import parse_command
from .config import AgentSettings, Settings
from .runtime import CliRuntimeRegistry
from .store import StateStore
from .task_executor import TaskExecutor
from .workspaces import WorkspaceManager


class ChatObserver(Protocol):
    def on_message_received(self, agent_id: str) -> None:
        ...

    def on_message_completed(
        self,
        agent_id: str,
        *,
        outcome: str,
        latency_ms: int,
    ) -> None:
        ...

    def on_message_failed(self, agent_id: str, *, latency_ms: int) -> None:
        ...


class ChatService:
    def __init__(
        self,
        settings: Settings,
        agent: AgentSettings,
        store: StateStore,
        workspace_manager: WorkspaceManager,
        cli_registry: CliRuntimeRegistry,
        communication_channel: BaseCommunicationChannel,
        task_executor: TaskExecutor,
        observer: ChatObserver | None = None,
    ) -> None:
        self.settings = settings
        self.agent = agent
        self.store = store
        self.communication_channel = communication_channel
        self.task_executor = task_executor
        self.observer = observer
        self._conversation_locks: Dict[str, asyncio.Lock] = {}
        self.command_handler = ChatCommandHandler(
            settings=settings,
            agent=agent,
            store=store,
            workspace_manager=workspace_manager,
            cli_registry=cli_registry,
            communication_channel=communication_channel,
        )

    async def handle_message(self, message: InboundMessage) -> None:
        started_at = asyncio.get_running_loop().time()
        if self.observer is not None:
            self.observer.on_message_received(self.agent.agent_id)
        if not self._is_allowed_user(message.owner_id):
            self._record_completion(started_at, outcome="ignored")
            return
        dedupe_key = "feishu:{}:{}".format(message.bot_app_id, message.message_id)
        if not self.store.remember_inbound_message(self.agent.agent_id, dedupe_key):
            self._record_completion(started_at, outcome="duplicate")
            return

        lock_key = "{}:{}".format(message.conversation_id, message.owner_id)
        lock = self._conversation_locks.setdefault(lock_key, asyncio.Lock())
        outcome = "handled"
        try:
            async with lock:
                command = parse_command(message.content)
                if command:
                    response = await self.command_handler.handle(message, command)
                    outcome = "command"
                    if response:
                        await self.communication_channel.send_text(message.reply_target, response)
                    return
                outcome = await self._handle_prompt(message)
        except Exception:
            latency_ms = int((asyncio.get_running_loop().time() - started_at) * 1000)
            if self.observer is not None:
                self.observer.on_message_failed(self.agent.agent_id, latency_ms=latency_ms)
            raise
        finally:
            if not lock.locked():
                self._conversation_locks.pop(lock_key, None)
        self._record_completion(started_at, outcome=outcome)

    def _record_completion(self, started_at: float, *, outcome: str) -> None:
        if self.observer is None:
            return
        latency_ms = int((asyncio.get_running_loop().time() - started_at) * 1000)
        self.observer.on_message_completed(
            self.agent.agent_id,
            outcome=outcome,
            latency_ms=latency_ms,
        )

    async def _handle_prompt(self, message: InboundMessage) -> str:
        workspace = self.command_handler.ensure_workspace()
        result = await self.task_executor.execute_prompt(
            workspace=workspace,
            prompt=message.content,
            conversation_id=message.conversation_id,
            conversation_owner_id=message.owner_id,
            reply_target=message.reply_target,
            announce_start=True,
            deliver_result=True,
        )
        if result.status != "succeeded":
            return "cli_failed"
        return "prompt"

    def _is_allowed_user(self, owner_id: str) -> bool:
        allow_from = self.agent.allow_from.strip()
        if not allow_from or allow_from == "*":
            return True
        allowed = {value.strip() for value in allow_from.split(",") if value.strip()}
        return owner_id in allowed
```

- [ ] **Step 3: Simplify workspaces.py — remove memory file bootstrapping**

Agents manage their own memory natively. Remove all `memory/*.md` file entries from `_workspace_files()` and the `memory/daily/` directory creation from `_bootstrap_workspace()`. Simplify `AGENTS.md` content to just agent identity.

In `_workspace_files()`, replace the current return dict with:

```python
return {
    "AGENTS.md": "\n".join(
        [
            "# AGENTS.md",
            "",
            "You are the agent assigned to this workspace.",
            f"- Agent ID: {agent_id}",
            f"- Agent name: {agent_name}",
            f"- Workspace name: {name}",
            f"- Workspace ID: {workspace_id}",
        ]
    )
    + "\n",
    ".light-claw/agent.json": json.dumps(agent_profile, indent=2) + "\n",
    ".light-claw/skills.md": "\n".join(
        [
            "# Agent Skills",
            "",
            "This file is the workspace-local skill policy for the current agent.",
            "Only use skills that are explicitly enabled here or by the referenced source file.",
            "",
            "Configured source:",
            str(skills_path) if skills_path else "(none configured)",
        ]
    )
    + "\n",
    ".light-claw/mcp.md": "\n".join(
        [
            "# Agent MCP",
            "",
            "This file records the MCP/tool profile allowed for the current agent.",
            "Treat it as the agent-local MCP contract before calling external tools.",
            "",
            "Configured source:",
            str(mcp_config_path) if mcp_config_path else "(none configured)",
        ]
    )
    + "\n",
}
```

In `_bootstrap_workspace()`, remove the line:

```python
(workspace_dir / "memory" / "daily").mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Verify**

```bash
uv run python -c "from light_claw.chat import ChatService; print('OK')"
uv run python -c "from light_claw.chat_commands import ChatCommandHandler; print('OK')"
uv run python -c "from light_claw.workspaces import WorkspaceManager; print('OK')"
```

- [ ] **Step 5: Commit**

```bash
git add src/light_claw/chat_commands.py src/light_claw/chat.py src/light_claw/workspaces.py
git commit -m "refactor: strip archive/task/cron/observation from chat layer, remove memory bootstrapping"
```

---

### Task 6: Simplify runtime_services

Remove all background service wiring. The `RuntimeServices` dataclass no longer carries archive/heartbeat/cron services. `RuntimeHealth` no longer tracks their state. Lifecycle functions become trivial.

**Files:**
- Modify: `src/light_claw/runtime_services.py`

- [ ] **Step 1: Rewrite runtime_services.py**

```python
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .chat import ChatObserver, ChatService
from .communication.base import BaseCommunicationChannel
from .communication.feishu import FeishuCommunicationChannel
from .config import APP_NAME, AgentSettings, Settings
from .runtime import CliRuntimeRegistry
from .store import StateStore
from .task_executor import TaskExecutor
from .workspaces import WorkspaceManager


log = logging.getLogger("light_claw.runtime_services")


@dataclass
class AgentRuntime:
    agent: AgentSettings
    cli_registry: CliRuntimeRegistry
    communication_channel: BaseCommunicationChannel
    task_executor: TaskExecutor
    chat_service: ChatService


@dataclass
class RuntimeServices:
    settings: Settings
    store: StateStore
    workspace_manager: WorkspaceManager
    health: "RuntimeHealth"
    agent_runtimes: dict[str, AgentRuntime]


class RuntimeHealth(ChatObserver):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started_at = time.time()
        self.background_error_count = 0
        self.message_counts = {
            "received": 0,
            "completed": 0,
            "failed": 0,
        }
        self.outcome_counts: dict[str, int] = {}
        self.agent_states = {
            agent.agent_id: {
                "app_id": agent.feishu_app_id,
                "connected": False,
                "last_event_at": None,
            }
            for agent in settings.agents
        }

    def mark_agent_connection(self, agent_id: str, connected: bool) -> None:
        state = self.agent_states.setdefault(
            agent_id,
            {"app_id": None, "connected": False, "last_event_at": None},
        )
        state["connected"] = connected

    def mark_agent_event(self, agent_id: str) -> None:
        state = self.agent_states.setdefault(
            agent_id,
            {"app_id": None, "connected": False, "last_event_at": None},
        )
        state["last_event_at"] = time.time()

    def mark_background_error(self) -> None:
        self.background_error_count += 1

    def on_message_received(self, agent_id: str) -> None:
        self.message_counts["received"] += 1
        self.mark_agent_event(agent_id)

    def on_message_completed(
        self,
        agent_id: str,
        *,
        outcome: str,
        latency_ms: int,
    ) -> None:
        self.message_counts["completed"] += 1
        self.outcome_counts[outcome] = self.outcome_counts.get(outcome, 0) + 1
        log.info(
            "message completed agent=%s outcome=%s latency_ms=%s",
            agent_id,
            outcome,
            latency_ms,
        )

    def on_message_failed(self, agent_id: str, *, latency_ms: int) -> None:
        self.message_counts["failed"] += 1
        log.exception("message failed agent=%s latency_ms=%s", agent_id, latency_ms)

    def snapshot(self, *, store_ok: bool) -> dict[str, Any]:
        if self.settings.feishu_enabled and self.settings.feishu_event_mode == "long_connection":
            agents_ready = all(
                bool(state["connected"]) for state in self.agent_states.values()
            )
        else:
            agents_ready = True
        ready = store_ok and agents_ready
        return {
            "app": APP_NAME,
            "started_at": self.started_at,
            "uptime_seconds": int(time.time() - self.started_at),
            "event_mode": self.settings.feishu_event_mode,
            "store_ok": store_ok,
            "agents": self.agent_states,
            "messages": self.message_counts,
            "outcomes": self.outcome_counts,
            "background_error_count": self.background_error_count,
            "ready": ready,
        }


def build_services(settings: Settings) -> RuntimeServices:
    store = StateStore(settings.database_path)
    store.prune_inbound_messages(settings.inbound_message_ttl_seconds)
    workspace_manager = WorkspaceManager(settings.workspaces_dir)
    health = RuntimeHealth(settings)

    agent_runtimes: dict[str, AgentRuntime] = {}
    for agent in settings.agents:
        cli_registry = CliRuntimeRegistry.from_settings(settings, agent)
        communication_channel = FeishuCommunicationChannel(
            agent_id=agent.agent_id,
            app_id=agent.feishu_app_id or "",
            app_secret=agent.feishu_app_secret or "",
            on_running_change=health.mark_agent_connection,
        )
        task_executor = TaskExecutor(
            settings=settings,
            agent=agent,
            store=store,
            cli_registry=cli_registry,
            communication_channel=communication_channel,
        )
        chat_service = ChatService(
            settings=settings,
            agent=agent,
            store=store,
            workspace_manager=workspace_manager,
            cli_registry=cli_registry,
            communication_channel=communication_channel,
            task_executor=task_executor,
            observer=health,
        )
        agent_runtimes[agent.agent_id] = AgentRuntime(
            agent=agent,
            cli_registry=cli_registry,
            communication_channel=communication_channel,
            task_executor=task_executor,
            chat_service=chat_service,
        )

    return RuntimeServices(
        settings=settings,
        store=store,
        workspace_manager=workspace_manager,
        health=health,
        agent_runtimes=agent_runtimes,
    )


async def start_managed_services(services: RuntimeServices) -> None:
    pass


async def shutdown_services(services: RuntimeServices) -> None:
    for runtime in services.agent_runtimes.values():
        await runtime.communication_channel.close()
    services.store.close()
```

- [ ] **Step 2: Verify**

```bash
uv run python -c "from light_claw.server import create_app; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
git add src/light_claw/runtime_services.py
git commit -m "refactor: remove all background services from runtime wiring"
```

---

### Task 7: Update all tests

Update the remaining test files to match the stripped codebase. Remove tests for deleted features; fix tests that reference removed parameters.

**Files:**
- Modify: `tests/test_server.py`
- Modify: `tests/test_store.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_commands.py`
- Modify: `tests/test_task_executor.py`
- Modify: `tests/test_workspaces.py`

- [ ] **Step 1: Rewrite test_server.py**

Remove `test_build_services_recovers_orphaned_task_runs_on_startup` entirely.

In `test_health_endpoints_are_ready_for_local_process`, remove env vars `LIGHT_CLAW_ARCHIVE_ENABLED`, `LIGHT_CLAW_TASK_HEARTBEAT_ENABLED`, `LIGHT_CLAW_CRON_ENABLED`. Remove assertions for `heartbeat` and `cron` in health details.

In `test_url_verification_uses_matching_agent_token`, remove `LIGHT_CLAW_ARCHIVE_ENABLED`.

```python
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from light_claw.config import Settings
from light_claw.server import create_app


class ServerTest(unittest.TestCase):
    def test_health_endpoints_are_ready_for_local_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_DATA_DIR": "",
                },
                clear=True,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir) / "repo")

            with TestClient(create_app(settings)) as client:
                self.assertEqual(client.get("/livez").status_code, 200)
                self.assertEqual(client.get("/healthz").json(), {"ok": True})
                ready_response = client.get("/readyz")
                self.assertEqual(ready_response.status_code, 200)
                self.assertTrue(ready_response.json()["ready"])

    def test_url_verification_uses_matching_agent_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            agents_file = Path(tmp_dir) / "agents.json"
            agents_file.write_text(
                """
                {
                  "agents": [
                    {
                      "agent_id": "writer",
                      "app_id": "cli_writer",
                      "app_secret": "writer_secret",
                      "verification_token": "writer_token"
                    },
                    {
                      "agent_id": "reviewer",
                      "app_id": "cli_reviewer",
                      "app_secret": "reviewer_secret",
                      "verification_token": "reviewer_token"
                    }
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "true",
                    "FEISHU_EVENT_MODE": "webhook",
                    "LIGHT_CLAW_AGENTS_FILE": str(agents_file),
                    "LIGHT_CLAW_DATA_DIR": "",
                },
                clear=True,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir) / "repo")

            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/feishu/events",
                    json={
                        "type": "url_verification",
                        "token": "reviewer_token",
                        "challenge": "ok",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"challenge": "ok"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Simplify test_store.py**

Remove `test_workspace_tasks_and_runs_round_trip`, `test_recover_orphaned_task_runs_marks_run_failed_and_releases_task`, `test_scheduled_tasks_can_be_created_listed_and_removed`, and `test_app_settings_round_trip`. Remove unused imports.

```python
import tempfile
import unittest
from pathlib import Path

from light_claw.models import WorkspaceRecord
from light_claw.store import StateStore


class StoreTest(unittest.TestCase):
    def test_agent_workspace_and_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            workspace = store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=Path(tmp_dir) / "default",
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            store.set_session_id(
                "agent-a",
                "conv_1",
                "ou_1",
                workspace.workspace_id,
                "session_1",
            )

            current = store.get_agent_workspace("agent-a")
            self.assertIsNotNone(current)
            self.assertEqual(current.agent_id, "agent-a")
            self.assertEqual(current.workspace_id, "default")
            self.assertEqual(
                store.get_workspace_session_id(
                    "agent-a",
                    "conv_1",
                    "ou_1",
                    workspace.workspace_id,
                ),
                "session_1",
            )

            store.clear_session("agent-a", "conv_1", "ou_1")
            self.assertIsNone(
                store.get_workspace_session_id(
                    "agent-a",
                    "conv_1",
                    "ou_1",
                    workspace.workspace_id,
                )
            )
            store.close()

    def test_updates_workspace_cli_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            workspace = store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=Path(tmp_dir) / "default",
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            updated = store.set_workspace_cli_provider(
                workspace.agent_id,
                workspace.owner_id,
                workspace.workspace_id,
                "codex",
            )
            self.assertIsNotNone(updated)
            self.assertEqual(updated.cli_provider, "codex")
            store.close()

    def test_deduplicates_inbound_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            self.assertTrue(store.remember_inbound_message("agent-a", "msg_1"))
            self.assertFalse(store.remember_inbound_message("agent-a", "msg_1"))
            self.assertTrue(store.remember_inbound_message("agent-b", "msg_1"))
            store.close()

    def test_agent_scoped_sessions_do_not_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            for agent_id in ("agent-a", "agent-b"):
                store.create_workspace(
                    WorkspaceRecord(
                        agent_id=agent_id,
                        owner_id="ou_1",
                        workspace_id="default",
                        name="Default",
                        path=Path(tmp_dir) / agent_id / "default",
                        cli_provider="codex",
                        created_at=0.0,
                        updated_at=0.0,
                    )
                )
                store.set_session_id(
                    agent_id,
                    "conv_1",
                    "ou_1",
                    "default",
                    f"session-{agent_id}",
                )

            self.assertEqual(
                store.get_workspace_session_id("agent-a", "conv_1", "ou_1", "default"),
                "session-agent-a",
            )
            self.assertEqual(
                store.get_workspace_session_id("agent-b", "conv_1", "ou_1", "default"),
                "session-agent-b",
            )
            store.close()

    def test_clear_workspace_sessions_removes_all_sessions_for_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=Path(tmp_dir) / "default",
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            store.set_session_id("agent-a", "conv_1", "ou_1", "default", "session-1")
            store.set_session_id("agent-a", "conv_2", "ou_2", "default", "session-2")

            store.clear_workspace_sessions("agent-a", "default")

            self.assertIsNone(
                store.get_workspace_session_id("agent-a", "conv_1", "ou_1", "default")
            )
            self.assertIsNone(
                store.get_workspace_session_id("agent-a", "conv_2", "ou_2", "default")
            )
            store.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Simplify test_config.py**

Remove `test_archive_defaults_to_sibling_directory` (tests removed fields), `test_archive_interval_must_be_positive`, `test_task_runtime_intervals_must_be_positive`.

- [ ] **Step 4: Simplify test_commands.py**

Remove `test_parse_task_and_cron_commands`. Update `test_help_text_omits_workspace_commands` to only assert on kept commands.

```python
import unittest

from light_claw.commands import help_text, parse_command


class CommandsTest(unittest.TestCase):
    def test_parse_cli_use(self) -> None:
        command = parse_command("/cli use codex")
        self.assertIsNotNone(command)
        self.assertEqual(command.kind, "cli_use")
        self.assertEqual(command.argument, "codex")

    def test_help_text_contains_kept_commands(self) -> None:
        text = help_text()
        self.assertNotIn("/workspace", text)
        self.assertNotIn("/archive", text)
        self.assertNotIn("/task", text)
        self.assertNotIn("/cron", text)
        self.assertIn("/cli list", text)
        self.assertIn("/cli use <provider>", text)
        self.assertIn("/reset", text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Rewrite test_task_executor.py**

Remove all tests that use removed features: `test_execute_workspace_task_records_run_and_reschedule`, `test_execute_workspace_task_records_progress_and_cron_guidance`, `test_task_create_starts_first_run_immediately`, `test_archive_daily_command_updates_backup_schedule`, `test_execute_prompt_injects_generic_observation_once`, `test_execute_prompt_injects_workspace_change_observation_on_resume`, `test_chat_command_observation_is_injected_on_next_prompt`.

Remove `WorkspaceArchiveService` import. Remove `archive_enabled`, `archive_dir`, `archive_interval_seconds`, `task_heartbeat_enabled`, `task_heartbeat_interval_seconds`, `cron_enabled`, `cron_poll_interval_seconds` from `_build_settings`.

Keep: `test_execute_prompt_persists_session_and_replies` and `test_cli_provider_switch_clears_existing_workspace_session`.

In `_build_settings`, the kept settings:

```python
def _build_settings(self, tmp_dir: str) -> Settings:
    return Settings(
        base_dir=Path(tmp_dir),
        host="127.0.0.1",
        port=8000,
        data_dir=Path(tmp_dir) / ".data",
        database_path=Path(tmp_dir) / ".data" / "state.db",
        workspaces_dir=Path(tmp_dir) / ".data" / "workspaces",
        claude_bin="claude",
        claude_model=None,
        claude_permission_mode="bypassPermissions",
        claude_add_dirs=[],
        codex_bin="codex",
        codex_model=None,
        codex_search=False,
        codex_sandbox="full-auto",
        codex_timeout_min_seconds=180,
        codex_timeout_max_seconds=900,
        codex_timeout_per_char_ms=80,
        codex_stall_timeout_seconds=120,
        codex_add_dirs=[],
        status_heartbeat_enabled=False,
        status_heartbeat_seconds=3600,
        inbound_message_ttl_seconds=60,
        default_cli_provider="codex",
        feishu_enabled=False,
        feishu_event_mode="webhook",
        feishu_app_id=None,
        feishu_app_secret=None,
        feishu_verification_token=None,
        allow_from="*",
        default_workspace_name="default",
        agents=(),
    )
```

In `test_execute_prompt_persists_session_and_replies`, the test stays the same but remove the `"Memory guidance:"` assertion if present (it's not — the test just checks session persistence and reply).

In `test_cli_provider_switch_clears_existing_workspace_session`, remove the `archive_service` parameter from the `ChatService` constructor.

- [ ] **Step 6: Update test_workspaces.py if needed**

Check if `test_workspaces.py` asserts existence of `memory/` files. If so, remove those assertions and add assertion that `memory/` directory is NOT created.

- [ ] **Step 7: Run all tests**

```bash
uv run python -m pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add tests/
git commit -m "refactor: update all tests for stripped-down chat MVP"
```

---

### Task 8: Clean up dependencies and documentation

Remove `croniter` dependency. Update README and CLAUDE.md.

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Remove croniter from pyproject.toml**

Remove `"croniter>=6.0.0",` from the `dependencies` list.

- [ ] **Step 2: Sync dependencies**

```bash
uv sync
```

- [ ] **Step 3: Update README.md**

Remove:
- All `/task`, `/cron`, `/archive` command references
- "Task system" section
- `background` subgraph from architecture diagram (heartbeat, cron, archive) and edges to `archive_dir`, `progress`, `observations`
- `LIGHT_CLAW_ARCHIVE_*`, `LIGHT_CLAW_TASK_HEARTBEAT_*`, `LIGHT_CLAW_CRON_*` env var docs
- Archive setup instructions
- `memory/` from workspace content listing
- `.light-claw/session-observations/` from workspace listing
- References to "observation queue", "session observations", "workspace file changes"

- [ ] **Step 4: Update CLAUDE.md**

Rewrite to reflect stripped MVP. Remove references to:
- Background services (archive, heartbeat, cron)
- Task system, schedule system
- Memory package and prompt enrichment pipeline
- All deleted files

- [ ] **Step 5: Run tests one final time**

```bash
uv run python -m pytest tests/ -v
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock README.md CLAUDE.md
git commit -m "refactor: remove croniter dependency and update docs for chat MVP"
```

---

## Summary of changes

**Deleted files (14):**
- `src/light_claw/archive.py`
- `src/light_claw/archive_sync.py`
- `src/light_claw/heartbeat.py`
- `src/light_claw/cron.py`
- `src/light_claw/schedule_state.py`
- `src/light_claw/task_commands.py`
- `src/light_claw/memory/__init__.py`
- `src/light_claw/memory/guidance.py`
- `src/light_claw/memory/session_observations.py`
- `src/light_claw/memory/task_progress.py`
- `tests/test_archive.py`
- `tests/test_heartbeat.py`
- `tests/test_cron.py`

**Simplified files (13):**
- `src/light_claw/models.py` — removed 3 dataclasses and 7 constants
- `src/light_claw/store_records.py` — removed 3 record mappers
- `src/light_claw/store.py` — removed 4 tables, 16 methods
- `src/light_claw/config.py` — removed 7 settings fields
- `src/light_claw/commands.py` — removed task/cron/archive parsing
- `src/light_claw/task_executor.py` — removed memory injection, observations, workspace task execution
- `src/light_claw/chat_commands.py` — removed archive/task/cron handling, observation recording
- `src/light_claw/chat.py` — removed `archive_service` parameter
- `src/light_claw/runtime_services.py` — removed 3 background services
- `src/light_claw/workspaces.py` — removed memory file bootstrapping
- `tests/test_server.py`, `tests/test_store.py`, `tests/test_commands.py`, `tests/test_config.py`, `tests/test_task_executor.py`

**Dependency removed:** `croniter`
