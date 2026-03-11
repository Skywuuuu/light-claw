from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class ReplyTarget:
    receive_id: str
    receive_id_type: str
    channel: str = "feishu"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InboundMessage:
    agent_id: str
    bot_app_id: str
    owner_id: str
    conversation_id: str
    message_id: str
    message_type: str
    content: str
    reply_target: ReplyTarget
    chat_id: Optional[str] = None
    chat_type: Optional[str] = None
    channel: str = "feishu"
    metadata: dict[str, Any] = field(default_factory=dict)
