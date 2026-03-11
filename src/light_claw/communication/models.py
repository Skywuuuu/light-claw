from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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
