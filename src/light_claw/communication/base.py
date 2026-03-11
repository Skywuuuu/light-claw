from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from concurrent.futures import Future
from typing import TYPE_CHECKING, Callable

from .messages import InboundMessage, ReplyTarget

if TYPE_CHECKING:
    from ..chat import ChatService


log = logging.getLogger("light_claw.communication.base")


class BaseCommunicationChannel(ABC):
    """Base IM channel interface for bidirectional communication adapters."""

    name: str = "base"

    def __init__(
        self,
        *,
        agent_id: str,
        on_running_change: Callable[[str, bool], None] | None = None,
    ) -> None:
        self.agent_id = agent_id
        self._on_running_change = on_running_change
        self._running = False
        self._chat_service: ChatService | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_inbound(
        self,
        *,
        chat_service: "ChatService",
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._chat_service = chat_service
        self._loop = loop

    @abstractmethod
    def start(self) -> None:
        """Start the communication channel and begin receiving inbound messages."""
        ...

    @abstractmethod
    async def send_text(self, target: ReplyTarget, content: str) -> None:
        """Send a text message through the communication channel."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any resources owned by the channel."""
        ...

    def stop(self) -> None:
        """Best-effort stop hook for blocking SDK-backed channels."""
        self._set_running(False)

    @property
    def is_running(self) -> bool:
        return self._running

    def _set_running(self, running: bool) -> None:
        self._running = running
        if self._on_running_change is not None:
            self._on_running_change(self.agent_id, running)

    def _require_inbound_binding(self) -> tuple["ChatService", asyncio.AbstractEventLoop]:
        if self._chat_service is None or self._loop is None:
            raise RuntimeError("Communication channel is not bound to an inbound runtime")
        return self._chat_service, self._loop

    def _handle_inbound_message(self, inbound: InboundMessage) -> None:
        self._require_inbound_binding()
        future = asyncio.run_coroutine_threadsafe(
            self._chat_service.handle_message(inbound),
            self._loop,
        )
        future.add_done_callback(_log_future_exception)


def _log_future_exception(future: Future[None]) -> None:
    try:
        future.result()
    except Exception:
        log.exception("background message handling failed")
