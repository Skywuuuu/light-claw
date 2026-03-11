from __future__ import annotations

from typing import Protocol

from .messages import ReplyTarget


class MessageSender(Protocol):
    async def send_text(self, target: ReplyTarget, content: str) -> None:
        ...

    async def close(self) -> None:
        ...
