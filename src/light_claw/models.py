from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

TASK_STATUS_PENDING = "pending"
TASK_STATUS_RUNNING = "running"
TASK_STATUS_SUCCEEDED = "succeeded"
TASK_STATUS_FAILED = "failed"
TASK_STATUS_CANCELLED = "cancelled"

SCHEDULE_KIND_INTERVAL = "interval"
SCHEDULE_KIND_CRON = "cron"


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
class FeishuReplyTarget:
    receive_id: str
    receive_id_type: str


@dataclass(frozen=True)
class FeishuInboundMessage:
    agent_id: str
    bot_app_id: str
    owner_id: str
    conversation_id: str
    message_id: str
    message_type: str
    content: str
    reply_target: FeishuReplyTarget
    chat_id: Optional[str] = None
    chat_type: Optional[str] = None


@dataclass(frozen=True)
class CliRunResult:
    session_id: Optional[str]
    answer: str
    raw_output: str


@dataclass(frozen=True)
class WorkspaceTaskRecord:
    agent_id: str
    owner_id: str
    workspace_id: str
    task_id: str
    title: str
    prompt: str
    status: str
    notify_conversation_id: Optional[str]
    notify_owner_id: Optional[str]
    notify_receive_id: Optional[str]
    notify_receive_id_type: Optional[str]
    last_run_at: Optional[float]
    next_run_at: Optional[float]
    last_error_message: Optional[str]
    last_result_excerpt: Optional[str]
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class ScheduledTaskRecord:
    agent_id: str
    owner_id: str
    workspace_id: str
    schedule_id: str
    task_id: str
    kind: str
    interval_seconds: Optional[int]
    cron_expr: Optional[str]
    enabled: bool
    next_run_at: Optional[float]
    last_run_at: Optional[float]
    last_error_message: Optional[str]
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class TaskRunRecord:
    agent_id: str
    owner_id: str
    workspace_id: str
    task_id: str
    run_id: str
    trigger_source: str
    status: str
    conversation_id: Optional[str]
    conversation_owner_id: Optional[str]
    started_at: float
    finished_at: Optional[float]
    error_message: Optional[str]
    result_excerpt: Optional[str]


@dataclass(frozen=True)
class CliProviderInfo:
    provider_id: str
    display_name: str
    description: str
    available: bool
