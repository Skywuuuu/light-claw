from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Optional

from .models import ConversationState, WorkspaceRecord


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
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS workspace (
                    owner_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    cli_provider TEXT NOT NULL DEFAULT 'codex',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(owner_id, workspace_id)
                );

                CREATE TABLE IF NOT EXISTS conversation_state (
                    conversation_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    workspace_id TEXT,
                    updated_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS conversation_session (
                    conversation_id TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    session_id TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(conversation_id, workspace_id)
                );

                CREATE TABLE IF NOT EXISTS inbound_message (
                    message_id TEXT PRIMARY KEY,
                    created_at REAL NOT NULL
                );
                """
            )
            self._ensure_workspace_columns()
            self._ensure_conversation_session_columns()
            self._db.commit()

    def _ensure_workspace_columns(self) -> None:
        columns = {
            str(row["name"])
            for row in self._db.execute("PRAGMA table_info(workspace)").fetchall()
        }
        if "cli_provider" not in columns:
            self._db.execute(
                """
                ALTER TABLE workspace
                ADD COLUMN cli_provider TEXT NOT NULL DEFAULT 'codex'
                """
            )

    def _ensure_conversation_session_columns(self) -> None:
        columns = {
            str(row["name"])
            for row in self._db.execute("PRAGMA table_info(conversation_session)").fetchall()
        }
        if "session_id" not in columns:
            self._db.execute(
                """
                ALTER TABLE conversation_session
                ADD COLUMN session_id TEXT
                """
            )
            if "thread_id" in columns:
                self._db.execute(
                    """
                    UPDATE conversation_session
                    SET session_id = thread_id
                    WHERE session_id IS NULL
                    """
                )

    def remember_inbound_message(self, message_id: str) -> bool:
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO inbound_message(message_id, created_at) VALUES(?, ?)",
                    (message_id, time.time()),
                )
                self._db.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def list_workspaces(self, owner_id: str) -> List[WorkspaceRecord]:
        with self._lock:
            rows = self._db.execute(
                """
                SELECT owner_id, workspace_id, name, path, cli_provider, created_at, updated_at
                FROM workspace
                WHERE owner_id = ?
                ORDER BY created_at ASC, workspace_id ASC
                """,
                (owner_id,),
            ).fetchall()
        return [self._row_to_workspace(row) for row in rows]

    def get_workspace(self, owner_id: str, workspace_id: str) -> Optional[WorkspaceRecord]:
        with self._lock:
            row = self._db.execute(
                """
                SELECT owner_id, workspace_id, name, path, cli_provider, created_at, updated_at
                FROM workspace
                WHERE owner_id = ? AND workspace_id = ?
                """,
                (owner_id, workspace_id),
            ).fetchone()
        return self._row_to_workspace(row) if row else None

    def create_workspace(self, workspace: WorkspaceRecord) -> WorkspaceRecord:
        now = time.time()
        created = WorkspaceRecord(
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
                INSERT INTO workspace(owner_id, workspace_id, name, path, cli_provider, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
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
        self, owner_id: str, workspace_id: str, cli_provider: str
    ) -> Optional[WorkspaceRecord]:
        now = time.time()
        with self._lock:
            cursor = self._db.execute(
                """
                UPDATE workspace
                SET cli_provider = ?, updated_at = ?
                WHERE owner_id = ? AND workspace_id = ?
                """,
                (cli_provider, now, owner_id, workspace_id),
            )
            self._db.commit()
        if cursor.rowcount == 0:
            return None
        return self.get_workspace(owner_id, workspace_id)

    def get_conversation_state(self, conversation_id: str) -> Optional[ConversationState]:
        with self._lock:
            row = self._db.execute(
                """
                SELECT
                    cs.conversation_id,
                    cs.owner_id,
                    cs.workspace_id,
                    sess.session_id,
                    cs.updated_at
                FROM conversation_state cs
                LEFT JOIN conversation_session sess
                    ON sess.conversation_id = cs.conversation_id
                   AND sess.workspace_id = cs.workspace_id
                WHERE cs.conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
        return self._row_to_conversation(row) if row else None

    def set_current_workspace(
        self, conversation_id: str, owner_id: str, workspace_id: str
    ) -> ConversationState:
        now = time.time()
        session_id = None
        with self._lock:
            self._db.execute(
                """
                INSERT INTO conversation_state(conversation_id, owner_id, workspace_id, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    workspace_id = excluded.workspace_id,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, owner_id, workspace_id, now),
            )
            self._db.execute(
                """
                UPDATE workspace
                SET updated_at = ?
                WHERE owner_id = ? AND workspace_id = ?
                """,
                (now, owner_id, workspace_id),
            )
            row = self._db.execute(
                """
                SELECT session_id
                FROM conversation_session
                WHERE conversation_id = ? AND workspace_id = ?
                """,
                (conversation_id, workspace_id),
            ).fetchone()
            if row and row["session_id"]:
                session_id = str(row["session_id"])
            self._db.commit()
        return ConversationState(
            conversation_id=conversation_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            session_id=session_id,
            updated_at=now,
        )

    def set_session_id(
        self, conversation_id: str, owner_id: str, workspace_id: str, session_id: str
    ) -> ConversationState:
        now = time.time()
        with self._lock:
            self._db.execute(
                """
                INSERT INTO conversation_state(conversation_id, owner_id, workspace_id, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    owner_id = excluded.owner_id,
                    workspace_id = excluded.workspace_id,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, owner_id, workspace_id, now),
            )
            self._db.execute(
                """
                INSERT INTO conversation_session(conversation_id, workspace_id, session_id, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(conversation_id, workspace_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, workspace_id, session_id, now),
            )
            self._db.execute(
                """
                UPDATE workspace
                SET updated_at = ?
                WHERE owner_id = ? AND workspace_id = ?
                """,
                (now, owner_id, workspace_id),
            )
            self._db.commit()
        return ConversationState(
            conversation_id=conversation_id,
            owner_id=owner_id,
            workspace_id=workspace_id,
            session_id=session_id,
            updated_at=now,
        )

    def clear_session(self, conversation_id: str) -> None:
        with self._lock:
            row = self._db.execute(
                """
                SELECT workspace_id
                FROM conversation_state
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            if not row or not row["workspace_id"]:
                return
            self._db.execute(
                """
                DELETE FROM conversation_session
                WHERE conversation_id = ? AND workspace_id = ?
                """,
                (conversation_id, row["workspace_id"]),
            )
            self._db.execute(
                """
                UPDATE conversation_state
                SET updated_at = ?
                WHERE conversation_id = ?
                """,
                (time.time(), conversation_id),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    @staticmethod
    def _row_to_workspace(row: sqlite3.Row) -> WorkspaceRecord:
        return WorkspaceRecord(
            owner_id=str(row["owner_id"]),
            workspace_id=str(row["workspace_id"]),
            name=str(row["name"]),
            path=Path(str(row["path"])),
            cli_provider=str(row["cli_provider"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )

    @staticmethod
    def _row_to_conversation(row: sqlite3.Row) -> ConversationState:
        return ConversationState(
            conversation_id=str(row["conversation_id"]),
            owner_id=str(row["owner_id"]),
            workspace_id=str(row["workspace_id"]) if row["workspace_id"] else None,
            session_id=str(row["session_id"]) if row["session_id"] else None,
            updated_at=float(row["updated_at"]),
        )
