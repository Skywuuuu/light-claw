from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional

import httpx
import lark_oapi as lark

from .base import BaseCommunicationChannel
from .messages import InboundMessage, ReplyTarget

if TYPE_CHECKING:
    from ..chat import ChatService


FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
MAX_TEXT_CHUNK_BYTES = 2000
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


log = logging.getLogger("light_claw.communication.feishu")


class FeishuCommunicationChannel(BaseCommunicationChannel):
    """Feishu communication channel for outbound replies and long-connection events."""

    name = "feishu"

    def __init__(
        self,
        *,
        agent_id: str,
        app_id: str,
        app_secret: str,
        timeout_seconds: float = 15.0,
        max_retries: int = 3,
        retry_delay_seconds: float = 1.0,
        on_running_change: Callable[[str, bool], None] | None = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            on_running_change=on_running_change,
        )
        self.app_id = app_id
        self.app_secret = app_secret
        self._http_client = httpx.AsyncClient(timeout=timeout_seconds)
        self._token_lock = asyncio.Lock()
        self._tenant_access_token: Optional[str] = None
        self._tenant_access_token_expires_at: float = 0.0
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self._ws_client = lark.ws.Client(
            app_id,
            app_secret,
            event_handler=self._build_event_handler(),
            log_level=lark.LogLevel.INFO,
        )

    def start(self) -> None:
        self._require_inbound_binding()
        log.info("Starting Feishu long connection client for agent %s", self.agent_id)
        self._set_running(True)
        try:
            self._ws_client.start()
        finally:
            self._set_running(False)

    async def close(self) -> None:
        await self._http_client.aclose()

    async def send_text(self, target: ReplyTarget, content: str) -> None:
        text = content.strip()
        if not text:
            raise ValueError("Feishu text content is required")
        if target.channel != "feishu":
            raise ValueError("Feishu sender only supports feishu reply targets")
        for chunk in split_text_by_utf8_bytes(text):
            await self._send_message(
                target=target,
                msg_type="text",
                content={"text": chunk},
            )

    async def _send_message(
        self,
        target: ReplyTarget,
        msg_type: str,
        content: Dict[str, Any],
    ) -> None:
        token = await self._get_tenant_access_token()
        response = await self._post_with_retry(
            FEISHU_API_BASE + "/im/v1/messages",
            params={"receive_id_type": target.receive_id_type},
            headers={"Authorization": "Bearer " + token},
            json={
                "receive_id": target.receive_id,
                "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False),
            },
        )
        payload = response.json()
        if payload.get("code") not in (0, None):
            raise RuntimeError(
                "Feishu send failed: {}".format(payload.get("msg") or payload)
            )

    async def _get_tenant_access_token(self) -> str:
        async with self._token_lock:
            now = asyncio.get_running_loop().time()
            if (
                self._tenant_access_token
                and now < self._tenant_access_token_expires_at - 30
            ):
                return self._tenant_access_token

            response = await self._post_with_retry(
                FEISHU_API_BASE + "/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self.app_id,
                    "app_secret": self.app_secret,
                },
            )
            payload = response.json()
            if payload.get("code") not in (0, None):
                raise RuntimeError(
                    "Feishu token fetch failed: {}".format(payload.get("msg") or payload)
                )
            token = payload.get("tenant_access_token")
            expire = int(payload.get("expire", 7200))
            if not isinstance(token, str) or not token:
                raise RuntimeError("Feishu token fetch returned no tenant_access_token")
            self._tenant_access_token = token
            self._tenant_access_token_expires_at = now + expire
            return token

    async def _post_with_retry(self, url: str, **kwargs: Any) -> httpx.Response:
        attempts = max(1, self.max_retries)
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self._http_client.post(url, **kwargs)
                if response.status_code not in RETRYABLE_STATUS_CODES:
                    response.raise_for_status()
                    return response
                last_error = httpx.HTTPStatusError(
                    f"retryable status code: {response.status_code}",
                    request=response.request,
                    response=response,
                )
            except httpx.HTTPError as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError):
                    status_code = exc.response.status_code
                    if status_code not in RETRYABLE_STATUS_CODES:
                        raise
            if attempt >= attempts:
                break
            delay = self.retry_delay_seconds * attempt
            log.warning(
                "Feishu request retrying",
                extra={
                    "app_id": self.app_id,
                    "attempt": attempt,
                    "url": url,
                },
            )
            await asyncio.sleep(delay)
        if last_error is None:
            raise RuntimeError("Feishu request failed without an exception")
        raise last_error

    def _build_event_handler(self) -> lark.EventDispatcherHandler:
        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_message_receive)
            .build()
        )

    def _handle_message_receive(self, event: lark.im.v1.P2ImMessageReceiveV1) -> None:
        inbound = parse_long_connection_message(
            event,
            agent_id=self.agent_id,
            bot_app_id=self.app_id,
        )
        if inbound is None:
            log.info("Ignored unsupported Feishu long connection event payload")
            return
        self._handle_inbound_message(inbound)


def split_text_by_utf8_bytes(content: str, max_bytes: int = MAX_TEXT_CHUNK_BYTES) -> list[str]:
    if not content:
        return [""]
    chunks = []
    current = []
    current_size = 0
    for char in content:
        encoded_size = len(char.encode("utf-8"))
        if current and current_size + encoded_size > max_bytes:
            chunks.append("".join(current))
            current = [char]
            current_size = encoded_size
            continue
        current.append(char)
        current_size += encoded_size
    if current:
        chunks.append("".join(current))
    return chunks


def verify_token(expected: Optional[str], actual: Optional[str]) -> bool:
    if not expected:
        return True
    return bool(actual) and actual == expected


def _build_inbound_message(
    agent_id: str,
    bot_app_id: str,
    owner_id: str,
    message_id: str,
    message_type: str,
    raw_content: str,
    chat_id: Optional[str],
    chat_type: Optional[str],
) -> Optional[InboundMessage]:
    content = normalize_inbound_content(message_type, raw_content)
    if not content:
        return None

    if chat_type == "p2p" or not isinstance(chat_id, str) or not chat_id:
        reply_target = ReplyTarget(receive_id=owner_id, receive_id_type="open_id")
        conversation_id = "feishu:user:" + owner_id
    else:
        reply_target = ReplyTarget(receive_id=chat_id, receive_id_type="chat_id")
        conversation_id = "feishu:chat:" + chat_id

    return InboundMessage(
        agent_id=agent_id,
        bot_app_id=bot_app_id,
        owner_id=owner_id,
        conversation_id=conversation_id,
        message_id=message_id,
        message_type=message_type,
        content=content,
        reply_target=reply_target,
        chat_id=chat_id if isinstance(chat_id, str) else None,
        chat_type=chat_type if isinstance(chat_type, str) else None,
    )


def parse_inbound_message(
    payload: Dict[str, Any],
    *,
    agent_id: str,
    bot_app_id: str,
) -> Optional[InboundMessage]:
    event = payload.get("event")
    if not isinstance(event, dict):
        return None

    message = event.get("message")
    sender = event.get("sender")
    if not isinstance(message, dict) or not isinstance(sender, dict):
        return None

    sender_id = sender.get("sender_id")
    if not isinstance(sender_id, dict):
        return None

    owner_id = sender_id.get("open_id")
    message_id = message.get("message_id")
    message_type = message.get("message_type")
    raw_content = message.get("content")
    chat_id = message.get("chat_id")
    chat_type = message.get("chat_type")

    if not isinstance(owner_id, str) or not isinstance(message_id, str):
        return None
    if not isinstance(message_type, str) or not isinstance(raw_content, str):
        return None

    return _build_inbound_message(
        agent_id=agent_id,
        bot_app_id=bot_app_id,
        owner_id=owner_id,
        message_id=message_id,
        message_type=message_type,
        raw_content=raw_content,
        chat_id=chat_id if isinstance(chat_id, str) else None,
        chat_type=chat_type if isinstance(chat_type, str) else None,
    )


def parse_long_connection_message(
    event: "lark.im.v1.P2ImMessageReceiveV1",
    *,
    agent_id: str,
    bot_app_id: str,
) -> Optional[InboundMessage]:
    body = getattr(event, "event", None)
    if body is None:
        return None

    sender = getattr(body, "sender", None)
    message = getattr(body, "message", None)
    if sender is None or message is None:
        return None

    sender_id = getattr(sender, "sender_id", None)
    owner_id = getattr(sender_id, "open_id", None)
    message_id = getattr(message, "message_id", None)
    message_type = getattr(message, "message_type", None)
    raw_content = getattr(message, "content", None)
    chat_id = getattr(message, "chat_id", None)
    chat_type = getattr(message, "chat_type", None)

    if not isinstance(owner_id, str) or not isinstance(message_id, str):
        return None
    if not isinstance(message_type, str) or not isinstance(raw_content, str):
        return None

    return _build_inbound_message(
        agent_id=agent_id,
        bot_app_id=bot_app_id,
        owner_id=owner_id,
        message_id=message_id,
        message_type=message_type,
        raw_content=raw_content,
        chat_id=chat_id if isinstance(chat_id, str) else None,
        chat_type=chat_type if isinstance(chat_type, str) else None,
    )


def normalize_inbound_content(message_type: str, raw_content: str) -> str:
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""

    normalized_type = message_type.strip().lower()
    if normalized_type == "text":
        text = payload.get("text")
        return text.strip() if isinstance(text, str) else ""
    if normalized_type == "post":
        return parse_post_content(payload)
    return "[Feishu {} message]\n{}".format(
        normalized_type,
        json.dumps(payload, ensure_ascii=False),
    )


def parse_post_content(payload: Dict[str, Any]) -> str:
    locale = None
    for value in payload.values():
        if isinstance(value, dict):
            locale = value
            break
    if locale is None:
        return ""
    lines = []
    title = locale.get("title")
    if isinstance(title, str) and title.strip():
        lines.append(title.strip())
    content_rows = locale.get("content")
    if not isinstance(content_rows, list):
        return "\n".join(lines).strip()
    for row in content_rows:
        if not isinstance(row, list):
            continue
        parts = []
        for item in row:
            if not isinstance(item, dict):
                continue
            if item.get("tag") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif item.get("tag") == "a":
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("href"), str):
                    parts.append(item["href"])
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()
