from __future__ import annotations

import asyncio
import logging
from concurrent.futures import Future

import lark_oapi as lark

from .chat import ChatService
from .config import Settings
from .feishu import parse_long_connection_message


log = logging.getLogger("light_claw.feishu_long_connection")


class FeishuLongConnectionClient:
    """Bridge Feishu long-connection events into the async chat service."""

    def __init__(
        self,
        settings: Settings,
        chat_service: ChatService,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._settings = settings
        self._chat_service = chat_service
        self._loop = loop
        self._client = lark.ws.Client(
            self._settings.feishu_app_id or "",
            self._settings.feishu_app_secret or "",
            event_handler=self._build_event_handler(),
            log_level=lark.LogLevel.INFO,
        )

    def start(self) -> None:
        """Start the blocking Feishu websocket client."""
        log.info("Starting Feishu long connection client")
        self._client.start()

    def _build_event_handler(self) -> lark.EventDispatcherHandler:
        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message_receive)
            .build()
        )

    def _handle_message_receive(self, event: lark.im.v1.P2ImMessageReceiveV1) -> None:
        inbound = parse_long_connection_message(event)
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
