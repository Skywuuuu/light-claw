from __future__ import annotations

from typing import Protocol

from .models import FeishuReplyTarget


class MessageSender(Protocol):
    async def send_text(self, target: FeishuReplyTarget, content: str) -> None:
        ...

    async def close(self) -> None:
        ...
