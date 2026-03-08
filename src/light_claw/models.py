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
class ConversationState:
    agent_id: str
    conversation_id: str
    owner_id: str
    workspace_id: Optional[str]
    session_id: Optional[str]
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
class CliProviderInfo:
    provider_id: str
    display_name: str
    description: str
    available: bool
