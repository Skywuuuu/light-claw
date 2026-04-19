from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Iterable, List, Optional

from .config import DEFAULT_AGENT_ID
from .models import WorkspaceRecord
from .store_records import row_to_workspace


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
        return row_to_workspace(row) if row else None

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
        return [row_to_workspace(row) for row in unique_rows]

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

    def clear_workspace_sessions(self, agent_id: str, workspace_id: str) -> None:
        with self._lock:
            self._db.execute(
                """
                DELETE FROM conversation_session
                WHERE agent_id = ?
                  AND workspace_id = ?
                """,
                (agent_id, workspace_id),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
