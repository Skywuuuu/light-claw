from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable, List, Optional

from .config import DEFAULT_AGENT_ID
from .models import (
    TASK_STATUS_FAILED,
    TASK_STATUS_RUNNING,
    ScheduledTaskRecord,
    TaskRunRecord,
    WorkspaceRecord,
    WorkspaceTaskRecord,
)


WORKSPACE_COLUMNS = {
    "agent_id",
    "owner_id",
    "workspace_id",
    "name",
    "path",
    "cli_provider",
    "created_at",
    "updated_at",
}
CONVERSATION_STATE_COLUMNS = {
    "agent_id",
    "conversation_id",
    "owner_id",
    "workspace_id",
    "updated_at",
}
CONVERSATION_SESSION_COLUMNS = {
    "agent_id",
    "conversation_id",
    "owner_id",
    "workspace_id",
    "session_id",
    "updated_at",
}
INBOUND_MESSAGE_COLUMNS = {"agent_id", "message_id", "created_at"}
WORKSPACE_TASK_COLUMNS = {
    "agent_id",
    "owner_id",
    "workspace_id",
    "task_id",
    "title",
    "prompt",
    "status",
    "notify_conversation_id",
    "notify_owner_id",
    "notify_receive_id",
    "notify_receive_id_type",
    "last_run_at",
    "next_run_at",
    "last_error_message",
    "last_result_excerpt",
    "created_at",
    "updated_at",
}
SCHEDULED_TASK_COLUMNS = {
    "agent_id",
    "owner_id",
    "workspace_id",
    "schedule_id",
    "task_id",
    "kind",
    "interval_seconds",
    "cron_expr",
    "enabled",
    "next_run_at",
    "last_run_at",
    "last_error_message",
    "created_at",
    "updated_at",
}
TASK_RUN_COLUMNS = {
    "agent_id",
    "owner_id",
    "workspace_id",
    "task_id",
    "run_id",
    "trigger_source",
    "status",
    "conversation_id",
    "conversation_owner_id",
    "started_at",
    "finished_at",
    "error_message",
    "result_excerpt",
}
APP_SETTING_COLUMNS = {
    "key",
    "value",
    "updated_at",
}


class StateStore:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path.resolve()
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.file_path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self._lock:
            if self._needs_agent_scope_migration():
                self._migrate_to_agent_scope()
            self._create_tables()
            self._db.commit()

    def _needs_agent_scope_migration(self) -> bool:
        return (
            self._table_exists("workspace")
            and "agent_id" not in self._table_columns("workspace")
        )

    def _table_exists(self, table_name: str) -> bool:
        row = self._db.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = ?
            """,
            (table_name,),
        ).fetchone()
        return row is not None

    def _table_columns(self, table_name: str) -> set[str]:
        if not self._table_exists(table_name):
            return set()
        return {
            str(row["name"])
            for row in self._db.execute(f"PRAGMA table_info({table_name})").fetchall()
        }

    def _migrate_to_agent_scope(self) -> None:
        legacy_tables = (
            "workspace",
            "conversation_state",
            "conversation_session",
            "inbound_message",
        )
        for table_name in legacy_tables:
            if self._table_exists(table_name):
                self._db.execute(f"ALTER TABLE {table_name} RENAME TO {table_name}_legacy")

        self._create_tables()
        self._migrate_workspace_legacy()
        self._migrate_conversation_state_legacy()
        self._migrate_conversation_session_legacy()
        self._migrate_inbound_message_legacy()

        for table_name in legacy_tables:
            legacy_name = f"{table_name}_legacy"
            if self._table_exists(legacy_name):
                self._db.execute(f"DROP TABLE {legacy_name}")

    def _create_tables(self) -> None:
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspace (
                agent_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                cli_provider TEXT NOT NULL DEFAULT 'codex',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(agent_id, owner_id, workspace_id)
            );

            CREATE TABLE IF NOT EXISTS conversation_state (
                agent_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                workspace_id TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY(agent_id, conversation_id, owner_id)
            );

            CREATE TABLE IF NOT EXISTS conversation_session (
                agent_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                session_id TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY(agent_id, conversation_id, owner_id, workspace_id)
            );

            CREATE TABLE IF NOT EXISTS inbound_message (
                agent_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY(agent_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS workspace_task (
                agent_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                title TEXT NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                notify_conversation_id TEXT,
                notify_owner_id TEXT,
                notify_receive_id TEXT,
                notify_receive_id_type TEXT,
                last_run_at REAL,
                next_run_at REAL,
                last_error_message TEXT,
                last_result_excerpt TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(agent_id, owner_id, workspace_id, task_id)
            );

            CREATE TABLE IF NOT EXISTS scheduled_task (
                agent_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                schedule_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                interval_seconds INTEGER,
                cron_expr TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                next_run_at REAL,
                last_run_at REAL,
                last_error_message TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY(agent_id, owner_id, workspace_id, schedule_id)
            );

            CREATE TABLE IF NOT EXISTS task_run (
                agent_id TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                task_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                trigger_source TEXT NOT NULL,
                status TEXT NOT NULL,
                conversation_id TEXT,
                conversation_owner_id TEXT,
                started_at REAL NOT NULL,
                finished_at REAL,
                error_message TEXT,
                result_excerpt TEXT,
                PRIMARY KEY(agent_id, run_id)
            );

            CREATE TABLE IF NOT EXISTS app_setting (
                key TEXT NOT NULL PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_workspace_task_lookup
            ON workspace_task(agent_id, owner_id, workspace_id, updated_at);

            CREATE INDEX IF NOT EXISTS idx_workspace_task_next_run
            ON workspace_task(agent_id, status, next_run_at);

            CREATE INDEX IF NOT EXISTS idx_scheduled_task_next_run
            ON scheduled_task(agent_id, enabled, next_run_at);

            CREATE INDEX IF NOT EXISTS idx_task_run_active
            ON task_run(agent_id, owner_id, workspace_id, task_id, status, started_at);
            """
        )

    def _migrate_workspace_legacy(self) -> None:
        if not self._table_exists("workspace_legacy"):
            return
        columns = self._table_columns("workspace_legacy")
        cli_provider_column = (
            "cli_provider" if "cli_provider" in columns else "'codex' AS cli_provider"
        )
        self._db.execute(
            f"""
            INSERT INTO workspace(
                agent_id,
                owner_id,
                workspace_id,
                name,
                path,
                cli_provider,
                created_at,
                updated_at
            )
            SELECT
                ?,
                owner_id,
                workspace_id,
                name,
                path,
                {cli_provider_column},
                created_at,
                updated_at
            FROM workspace_legacy
            """,
            (DEFAULT_AGENT_ID,),
        )

    def _migrate_conversation_state_legacy(self) -> None:
        if not self._table_exists("conversation_state_legacy"):
            return
        self._db.execute(
            """
            INSERT INTO conversation_state(
                agent_id,
                conversation_id,
                owner_id,
                workspace_id,
                updated_at
            )
            SELECT ?, conversation_id, owner_id, workspace_id, updated_at
            FROM conversation_state_legacy
            """,
            (DEFAULT_AGENT_ID,),
        )

    def _migrate_conversation_session_legacy(self) -> None:
        if not self._table_exists("conversation_session_legacy"):
            return
        columns = self._table_columns("conversation_session_legacy")
        session_column = (
            "session_id"
            if "session_id" in columns
            else "thread_id AS session_id"
            if "thread_id" in columns
            else "NULL AS session_id"
        )
        owner_column = (
            "cs.owner_id"
            if self._table_exists("conversation_state_legacy")
            else f"'{DEFAULT_AGENT_ID}'"
        )
        self._db.execute(
            f"""
            INSERT INTO conversation_session(
                agent_id,
                conversation_id,
                owner_id,
                workspace_id,
                session_id,
                updated_at
            )
            SELECT
                ?,
                sess.conversation_id,
                {owner_column},
                sess.workspace_id,
                {session_column},
                sess.updated_at
            FROM conversation_session_legacy sess
            LEFT JOIN conversation_state_legacy cs
                ON cs.conversation_id = sess.conversation_id
            """,
            (DEFAULT_AGENT_ID,),
        )

    def _migrate_inbound_message_legacy(self) -> None:
        if not self._table_exists("inbound_message_legacy"):
            return
        self._db.execute(
            """
            INSERT INTO inbound_message(agent_id, message_id, created_at)
            SELECT ?, message_id, created_at
            FROM inbound_message_legacy
            """,
            (DEFAULT_AGENT_ID,),
        )

    def ping(self) -> bool:
        with self._lock:
            row = self._db.execute("SELECT 1").fetchone()
        return row is not None

    def prune_inbound_messages(self, max_age_seconds: int) -> int:
        cutoff = time.time() - max_age_seconds
        with self._lock:
            cursor = self._db.execute(
                "DELETE FROM inbound_message WHERE created_at < ?",
                (cutoff,),
            )
            self._db.commit()
        return int(cursor.rowcount or 0)

    def remember_inbound_message(self, agent_id: str, message_id: str) -> bool:
        with self._lock:
            try:
                self._db.execute(
                    """
                    INSERT INTO inbound_message(agent_id, message_id, created_at)
                    VALUES(?, ?, ?)
                    """,
                    (agent_id, message_id, time.time()),
                )
                self._db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_app_setting(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                """
                SELECT value
                FROM app_setting
                WHERE key = ?
                """,
                (key,),
            ).fetchone()
        if row is None:
            return None
        return str(row["value"])

    def set_app_setting(self, key: str, value: str) -> str:
        now = time.time()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO app_setting(key, value, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(key)
                DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, now),
            )
            self._db.commit()
        return value

    def get_agent_workspace(self, agent_id: str) -> Optional[WorkspaceRecord]:
        with self._lock:
            row = self._db.execute(
                """
                SELECT
                    agent_id,
                    owner_id,
                    workspace_id,
                    name,
                    path,
                    cli_provider,
                    created_at,
                    updated_at
                FROM workspace
                WHERE agent_id = ?
                ORDER BY updated_at DESC, created_at DESC, workspace_id ASC
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
        return self._row_to_workspace(row) if row else None

    def list_all_workspaces(self) -> List[WorkspaceRecord]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT
                    agent_id,
                    owner_id,
                    workspace_id,
                    name,
                    path,
                    cli_provider,
                    created_at,
                    updated_at
                FROM workspace
                ORDER BY agent_id ASC, updated_at DESC, created_at DESC, workspace_id ASC
                """
            ).fetchall()
        unique_rows: list[sqlite3.Row] = []
        seen_agents: set[str] = set()
        for row in rows:
            agent_id = str(row["agent_id"])
            if agent_id in seen_agents:
                continue
            seen_agents.add(agent_id)
            unique_rows.append(row)
        return [self._row_to_workspace(row) for row in unique_rows]

    def create_workspace(self, workspace: WorkspaceRecord) -> WorkspaceRecord:
        now = time.time()
        created = WorkspaceRecord(
            agent_id=workspace.agent_id,
            owner_id=workspace.owner_id,
            workspace_id=workspace.workspace_id,
            name=workspace.name,
            path=workspace.path.resolve(),
            cli_provider=workspace.cli_provider,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._db.execute(
                """
                INSERT INTO workspace(
                    agent_id,
                    owner_id,
                    workspace_id,
                    name,
                    path,
                    cli_provider,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created.agent_id,
                    created.owner_id,
                    created.workspace_id,
                    created.name,
                    str(created.path),
                    created.cli_provider,
                    created.created_at,
                    created.updated_at,
                ),
            )
            self._db.commit()
        return created

    def set_workspace_cli_provider(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
        cli_provider: str,
    ) -> Optional[WorkspaceRecord]:
        now = time.time()
        with self._lock:
            cursor = self._db.execute(
                """
                UPDATE workspace
                SET cli_provider = ?, updated_at = ?
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ?
                """,
                (cli_provider, now, agent_id, owner_id, workspace_id),
            )
            self._db.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_agent_workspace(agent_id)

    def get_workspace_session_id(
        self,
        agent_id: str,
        conversation_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> Optional[str]:
        with self._lock:
            row = self._db.execute(
                """
                SELECT session_id
                FROM conversation_session
                WHERE agent_id = ?
                  AND conversation_id = ?
                  AND owner_id = ?
                  AND workspace_id = ?
                """,
                (agent_id, conversation_id, owner_id, workspace_id),
            ).fetchone()
        if row is None or not row["session_id"]:
            return None
        return str(row["session_id"])

    def set_session_id(
        self,
        agent_id: str,
        conversation_id: str,
        owner_id: str,
        workspace_id: str,
        session_id: str,
    ) -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO conversation_session(
                    agent_id,
                    conversation_id,
                    owner_id,
                    workspace_id,
                    session_id,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, conversation_id, owner_id, workspace_id)
                DO UPDATE SET
                    session_id = excluded.session_id,
                    updated_at = excluded.updated_at
                """,
                (agent_id, conversation_id, owner_id, workspace_id, session_id, now),
            )
            self._db.execute(
                """
                UPDATE workspace
                SET updated_at = ?
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ?
                """,
                (now, agent_id, owner_id, workspace_id),
            )
            self._db.commit()

    def clear_session(self, agent_id: str, conversation_id: str, owner_id: str) -> None:
        with self._lock:
            self._db.execute(
                """
                DELETE FROM conversation_session
                WHERE agent_id = ?
                  AND conversation_id = ?
                  AND owner_id = ?
                """,
                (agent_id, conversation_id, owner_id),
            )
            self._db.commit()

    def create_workspace_task(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
        prompt: str,
        *,
        title: str | None = None,
        status: str = TASK_STATUS_RUNNING,
        notify_conversation_id: str | None = None,
        notify_owner_id: str | None = None,
        notify_receive_id: str | None = None,
        notify_receive_id_type: str | None = None,
        next_run_at: float | None = None,
    ) -> WorkspaceTaskRecord:
        now = time.time()
        task_id = str(uuid.uuid4())[:8]
        prompt_lines = prompt.strip().splitlines()
        inferred_title = prompt_lines[0] if prompt_lines else task_id
        record = WorkspaceTaskRecord(
            agent_id=agent_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
            title=(title or inferred_title)[:80],
            prompt=prompt,
            status=status,
            notify_conversation_id=notify_conversation_id,
            notify_owner_id=notify_owner_id,
            notify_receive_id=notify_receive_id,
            notify_receive_id_type=notify_receive_id_type,
            last_run_at=None,
            next_run_at=next_run_at if next_run_at is not None else now,
            last_error_message=None,
            last_result_excerpt=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._db.execute(
                """
                INSERT INTO workspace_task(
                    agent_id,
                    owner_id,
                    workspace_id,
                    task_id,
                    title,
                    prompt,
                    status,
                    notify_conversation_id,
                    notify_owner_id,
                    notify_receive_id,
                    notify_receive_id_type,
                    last_run_at,
                    next_run_at,
                    last_error_message,
                    last_result_excerpt,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.agent_id,
                    record.owner_id,
                    record.workspace_id,
                    record.task_id,
                    record.title,
                    record.prompt,
                    record.status,
                    record.notify_conversation_id,
                    record.notify_owner_id,
                    record.notify_receive_id,
                    record.notify_receive_id_type,
                    record.last_run_at,
                    record.next_run_at,
                    record.last_error_message,
                    record.last_result_excerpt,
                    record.created_at,
                    record.updated_at,
                ),
            )
            self._db.commit()
        return record

    def list_workspace_tasks(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> List[WorkspaceTaskRecord]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT *
                FROM workspace_task
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ?
                ORDER BY created_at ASC, task_id ASC
                """,
                (agent_id, owner_id, workspace_id),
            ).fetchall()
        return [self._row_to_workspace_task(row) for row in rows]

    def list_due_workspace_tasks(
        self,
        max_next_run_at: float,
    ) -> List[WorkspaceTaskRecord]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT *
                FROM workspace_task
                WHERE status IN (?, ?)
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                ORDER BY next_run_at ASC, created_at ASC
                """,
                (TASK_STATUS_RUNNING, TASK_STATUS_FAILED, max_next_run_at),
            ).fetchall()
        return [self._row_to_workspace_task(row) for row in rows]

    def get_workspace_task(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
        task_id: str,
    ) -> Optional[WorkspaceTaskRecord]:
        with self._lock:
            row = self._db.execute(
                """
                SELECT *
                FROM workspace_task
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND task_id = ?
                """,
                (agent_id, owner_id, workspace_id, task_id),
            ).fetchone()
        return self._row_to_workspace_task(row) if row else None

    def get_latest_task_run(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
        task_id: str,
    ) -> Optional[TaskRunRecord]:
        with self._lock:
            row = self._db.execute(
                """
                SELECT *
                FROM task_run
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND task_id = ?
                ORDER BY started_at DESC, run_id DESC
                LIMIT 1
                """,
                (agent_id, owner_id, workspace_id, task_id),
            ).fetchone()
        return self._row_to_task_run(row) if row else None

    def update_workspace_task(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        *,
        status: str | None = None,
        next_run_at: float | None = None,
        last_error_message: str | None = None,
        last_result_excerpt: str | None = None,
    ) -> Optional[WorkspaceTaskRecord]:
        now = time.time()
        with self._lock:
            row = self._db.execute(
                """
                SELECT *
                FROM workspace_task
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND task_id = ?
                """,
                (agent_id, owner_id, workspace_id, task_id),
            ).fetchone()
            if row is None:
                return None
            current = self._row_to_workspace_task(row)
            self._db.execute(
                """
                UPDATE workspace_task
                SET status = ?,
                    next_run_at = ?,
                    last_error_message = ?,
                    last_result_excerpt = ?,
                    updated_at = ?
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND task_id = ?
                """,
                (
                    status or current.status,
                    next_run_at,
                    last_error_message,
                    last_result_excerpt,
                    now,
                    agent_id,
                    owner_id,
                    workspace_id,
                    task_id,
                ),
            )
            self._db.commit()
        return self.get_workspace_task(agent_id, owner_id, workspace_id, task_id)

    def create_scheduled_task(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        *,
        kind: str,
        interval_seconds: int | None = None,
        cron_expr: str | None = None,
        next_run_at: float | None = None,
    ) -> ScheduledTaskRecord:
        now = time.time()
        schedule_id = str(uuid.uuid4())[:8]
        record = ScheduledTaskRecord(
            agent_id=agent_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            schedule_id=schedule_id,
            task_id=task_id,
            kind=kind,
            interval_seconds=interval_seconds,
            cron_expr=cron_expr,
            enabled=True,
            next_run_at=next_run_at,
            last_run_at=None,
            last_error_message=None,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._db.execute(
                """
                INSERT INTO scheduled_task(
                    agent_id,
                    owner_id,
                    workspace_id,
                    schedule_id,
                    task_id,
                    kind,
                    interval_seconds,
                    cron_expr,
                    enabled,
                    next_run_at,
                    last_run_at,
                    last_error_message,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.agent_id,
                    record.owner_id,
                    record.workspace_id,
                    record.schedule_id,
                    record.task_id,
                    record.kind,
                    record.interval_seconds,
                    record.cron_expr,
                    1 if record.enabled else 0,
                    record.next_run_at,
                    record.last_run_at,
                    record.last_error_message,
                    record.created_at,
                    record.updated_at,
                ),
            )
            self._db.commit()
        return record

    def list_scheduled_tasks(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
    ) -> List[ScheduledTaskRecord]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT *
                FROM scheduled_task
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ?
                ORDER BY created_at ASC, schedule_id ASC
                """,
                (agent_id, owner_id, workspace_id),
            ).fetchall()
        return [self._row_to_scheduled_task(row) for row in rows]

    def list_due_scheduled_tasks(self, max_next_run_at: float) -> List[ScheduledTaskRecord]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT *
                FROM scheduled_task
                WHERE enabled = 1
                  AND next_run_at IS NOT NULL
                  AND next_run_at <= ?
                ORDER BY next_run_at ASC, created_at ASC
                """,
                (max_next_run_at,),
            ).fetchall()
        return [self._row_to_scheduled_task(row) for row in rows]

    def remove_scheduled_task(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
        schedule_id: str,
    ) -> bool:
        with self._lock:
            cursor = self._db.execute(
                """
                DELETE FROM scheduled_task
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND schedule_id = ?
                """,
                (agent_id, owner_id, workspace_id, schedule_id),
            )
            self._db.commit()
        return bool(cursor.rowcount)

    def claim_workspace_task(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
        task_id: str,
        *,
        trigger_source: str,
        conversation_id: str | None = None,
        conversation_owner_id: str | None = None,
    ) -> Optional[TaskRunRecord]:
        now = time.time()
        run_id = str(uuid.uuid4())[:8]
        with self._lock:
            row = self._db.execute(
                """
                SELECT *
                FROM workspace_task
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND task_id = ?
                """,
                (agent_id, owner_id, workspace_id, task_id),
            ).fetchone()
            if row is None:
                return None
            active_run = self._db.execute(
                """
                SELECT run_id
                FROM task_run
                WHERE agent_id = ?
                  AND owner_id = ?
                  AND workspace_id = ?
                  AND task_id = ?
                  AND status = ?
                LIMIT 1
                """,
                (agent_id, owner_id, workspace_id, task_id, TASK_STATUS_RUNNING),
            ).fetchone()
            if active_run is not None:
                return None
            self._db.execute(
                """
                INSERT INTO task_run(
                    agent_id,
                    owner_id,
                    workspace_id,
                    task_id,
                    run_id,
                    trigger_source,
                    status,
                    conversation_id,
                    conversation_owner_id,
                    started_at,
                    finished_at,
                    error_message,
                    result_excerpt
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                """,
                (
                    agent_id,
                    owner_id,
                    workspace_id,
                    task_id,
                    run_id,
                    trigger_source,
                    TASK_STATUS_RUNNING,
                    conversation_id,
                    conversation_owner_id,
                    now,
                ),
            )
            self._db.execute(
                """
                UPDATE workspace_task
                SET status = ?,
                    last_error_message = NULL,
                    updated_at = ?
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND task_id = ?
                """,
                (TASK_STATUS_RUNNING, now, agent_id, owner_id, workspace_id, task_id),
            )
            self._db.commit()
        return TaskRunRecord(
            agent_id=agent_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            task_id=task_id,
            run_id=run_id,
            trigger_source=trigger_source,
            status=TASK_STATUS_RUNNING,
            conversation_id=conversation_id,
            conversation_owner_id=conversation_owner_id,
            started_at=now,
            finished_at=None,
            error_message=None,
            result_excerpt=None,
        )

    def complete_task_run(
        self,
        agent_id: str,
        run_id: str,
        *,
        status: str,
        task_status: str | None = None,
        error_message: str | None = None,
        result_excerpt: str | None = None,
        next_run_at: float | None = None,
    ) -> Optional[TaskRunRecord]:
        finished_at = time.time()
        with self._lock:
            row = self._db.execute(
                """
                SELECT *
                FROM task_run
                WHERE agent_id = ? AND run_id = ?
                """,
                (agent_id, run_id),
            ).fetchone()
            if row is None:
                return None
            run = self._row_to_task_run(row)
            self._db.execute(
                """
                UPDATE task_run
                SET status = ?,
                    finished_at = ?,
                    error_message = ?,
                    result_excerpt = ?
                WHERE agent_id = ? AND run_id = ?
                """,
                (status, finished_at, error_message, result_excerpt, agent_id, run_id),
            )
            self._db.execute(
                """
                UPDATE workspace_task
                SET status = ?,
                    last_run_at = ?,
                    next_run_at = ?,
                    last_error_message = ?,
                    last_result_excerpt = ?,
                    updated_at = ?
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND task_id = ?
                """,
                (
                    task_status or status,
                    finished_at,
                    next_run_at,
                    error_message,
                    result_excerpt,
                    finished_at,
                    run.agent_id,
                    run.owner_id,
                    run.workspace_id,
                    run.task_id,
                ),
            )
            self._db.commit()
        return TaskRunRecord(
            agent_id=run.agent_id,
            owner_id=run.owner_id,
            workspace_id=run.workspace_id,
            task_id=run.task_id,
            run_id=run.run_id,
            trigger_source=run.trigger_source,
            status=status,
            conversation_id=run.conversation_id,
            conversation_owner_id=run.conversation_owner_id,
            started_at=run.started_at,
            finished_at=finished_at,
            error_message=error_message,
            result_excerpt=result_excerpt,
        )

    def recover_orphaned_task_runs(
        self,
        error_message: str = "Recovered orphaned task run from a previous process.",
    ) -> int:
        finished_at = time.time()
        with self._lock:
            rows = self._db.execute(
                """
                SELECT
                    run.agent_id,
                    run.owner_id,
                    run.workspace_id,
                    run.task_id,
                    run.run_id,
                    task.next_run_at
                FROM task_run run
                JOIN workspace_task task
                  ON task.agent_id = run.agent_id
                 AND task.owner_id = run.owner_id
                 AND task.workspace_id = run.workspace_id
                 AND task.task_id = run.task_id
                WHERE run.status = ?
                """,
                (TASK_STATUS_RUNNING,),
            ).fetchall()
            for row in rows:
                self._db.execute(
                    """
                    UPDATE task_run
                    SET status = ?,
                        finished_at = ?,
                        error_message = ?,
                        result_excerpt = NULL
                    WHERE agent_id = ? AND run_id = ?
                    """,
                    (
                        TASK_STATUS_FAILED,
                        finished_at,
                        error_message,
                        str(row["agent_id"]),
                        str(row["run_id"]),
                    ),
                )
                self._db.execute(
                    """
                    UPDATE workspace_task
                    SET status = ?,
                        last_run_at = ?,
                        next_run_at = ?,
                        last_error_message = ?,
                        last_result_excerpt = NULL,
                        updated_at = ?
                    WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND task_id = ?
                    """,
                    (
                        TASK_STATUS_FAILED,
                        finished_at,
                        row["next_run_at"],
                        error_message,
                        finished_at,
                        str(row["agent_id"]),
                        str(row["owner_id"]),
                        str(row["workspace_id"]),
                        str(row["task_id"]),
                    ),
                )
            self._db.commit()
        return len(rows)

    def update_scheduled_task_run(
        self,
        agent_id: str,
        owner_id: str,
        workspace_id: str,
        schedule_id: str,
        *,
        next_run_at: float | None,
        last_run_at: float | None = None,
        last_error_message: str | None = None,
        enabled: bool | None = None,
    ) -> Optional[ScheduledTaskRecord]:
        now = time.time()
        with self._lock:
            row = self._db.execute(
                """
                SELECT *
                FROM scheduled_task
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND schedule_id = ?
                """,
                (agent_id, owner_id, workspace_id, schedule_id),
            ).fetchone()
            if row is None:
                return None
            current = self._row_to_scheduled_task(row)
            new_enabled = current.enabled if enabled is None else enabled
            self._db.execute(
                """
                UPDATE scheduled_task
                SET enabled = ?,
                    next_run_at = ?,
                    last_run_at = ?,
                    last_error_message = ?,
                    updated_at = ?
                WHERE agent_id = ? AND owner_id = ? AND workspace_id = ? AND schedule_id = ?
                """,
                (
                    1 if new_enabled else 0,
                    next_run_at,
                    last_run_at,
                    last_error_message,
                    now,
                    agent_id,
                    owner_id,
                    workspace_id,
                    schedule_id,
                ),
            )
            self._db.commit()
        return ScheduledTaskRecord(
            agent_id=current.agent_id,
            owner_id=current.owner_id,
            workspace_id=current.workspace_id,
            schedule_id=current.schedule_id,
            task_id=current.task_id,
            kind=current.kind,
            interval_seconds=current.interval_seconds,
            cron_expr=current.cron_expr,
            enabled=new_enabled,
            next_run_at=next_run_at,
            last_run_at=last_run_at,
            last_error_message=last_error_message,
            created_at=current.created_at,
            updated_at=now,
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row_to_workspace(row: sqlite3.Row) -> WorkspaceRecord:
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

    @staticmethod
    def _row_to_workspace_task(row: sqlite3.Row) -> WorkspaceTaskRecord:
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
                str(row["last_result_excerpt"])
                if row["last_result_excerpt"]
                else None
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _row_to_scheduled_task(row: sqlite3.Row) -> ScheduledTaskRecord:
        return ScheduledTaskRecord(
            agent_id=str(row["agent_id"]),
            owner_id=str(row["owner_id"]),
            workspace_id=str(row["workspace_id"]),
            schedule_id=str(row["schedule_id"]),
            task_id=str(row["task_id"]),
            kind=str(row["kind"]),
            interval_seconds=(
                int(row["interval_seconds"]) if row["interval_seconds"] is not None else None
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

    @staticmethod
    def _row_to_task_run(row: sqlite3.Row) -> TaskRunRecord:
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
                str(row["conversation_owner_id"])
                if row["conversation_owner_id"]
                else None
            ),
            started_at=float(row["started_at"]),
            finished_at=float(row["finished_at"]) if row["finished_at"] else None,
            error_message=str(row["error_message"]) if row["error_message"] else None,
            result_excerpt=str(row["result_excerpt"]) if row["result_excerpt"] else None,
        )
