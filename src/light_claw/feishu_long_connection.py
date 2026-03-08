from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future
from typing import Callable

import lark_oapi as lark

from .chat import ChatService
from .feishu import parse_long_connection_message


log = logging.getLogger("light_claw.feishu_long_connection")


class FeishuLongConnectionClient:
    """Bridge Feishu long-connection events into the async chat service."""

    def __init__(
        self,
        agent_id: str,
        app_id: str,
        app_secret: str,
        chat_service: ChatService,
        loop: asyncio.AbstractEventLoop,
        on_running_change: Callable[[str, bool], None] | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._app_id = app_id
        self._chat_service = chat_service
        self._loop = loop
        self._on_running_change = on_running_change
        self._client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=self._build_event_handler(),
            log_level=lark.LogLevel.INFO,
        )

    def start(self) -> None:
        """Start the blocking Feishu websocket client."""
        log.info("Starting Feishu long connection client for agent %s", self._agent_id)
        if self._on_running_change is not None:
            self._on_running_change(self._agent_id, True)
        try:
            self._client.start()
        finally:
            if self._on_running_change is not None:
                self._on_running_change(self._agent_id, False)

    def _build_event_handler(self) -> lark.EventDispatcherHandler:
        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message_receive)
            .build()
        )

    def _handle_message_receive(self, event: lark.im.v1.P2ImMessageReceiveV1) -> None:
        inbound = parse_long_connection_message(
            event,
            agent_id=self._agent_id,
            bot_app_id=self._app_id,
        )
        if inbound is None:
            log.info("Ignored unsupported Feishu long connection event payload")
            return

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
