from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from concurrent.futures import Future
from typing import TYPE_CHECKING, Callable

from .models import FeishuInboundMessage

if TYPE_CHECKING:
    from ..chat import ChatService


log = logging.getLogger("light_claw.communication.base")


class IMLongConnectionClient(ABC):
    def __init__(
        self,
        *,
        agent_id: str,
        chat_service: "ChatService",
        loop: asyncio.AbstractEventLoop,
        on_running_change: Callable[[str, bool], None] | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._chat_service = chat_service
        self._loop = loop
        self._on_running_change = on_running_change

    @abstractmethod
    def start(self) -> None:
        ...

    def _mark_running(self, connected: bool) -> None:
        if self._on_running_change is not None:
            self._on_running_change(self._agent_id, connected)

    def _submit_inbound(self, inbound: FeishuInboundMessage) -> None:
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
