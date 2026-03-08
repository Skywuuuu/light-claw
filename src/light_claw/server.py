from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from .chat import ChatService
from .cli_runners import CliRunnerRegistry
from .config import APP_NAME, Settings
from .feishu import FeishuClient, parse_inbound_message, verify_token
from .feishu_long_connection import FeishuLongConnectionClient
from .store import StateStore
from .workspaces import WorkspaceManager


log = logging.getLogger("light_claw.server")


def build_services(settings: Settings) -> Dict[str, Any]:
    store = StateStore(settings.database_path)
    workspace_manager = WorkspaceManager(settings.workspaces_dir)
    cli_registry = CliRunnerRegistry.from_settings(settings)
    feishu_client = FeishuClient(
        app_id=settings.feishu_app_id or "",
        app_secret=settings.feishu_app_secret or "",
    )
    chat_service = ChatService(
        settings=settings,
        store=store,
        workspace_manager=workspace_manager,
        cli_registry=cli_registry,
        feishu_client=feishu_client,
    )
    return {
        "store": store,
        "workspace_manager": workspace_manager,
        "cli_registry": cli_registry,
        "feishu_client": feishu_client,
        "chat_service": chat_service,
    }


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        services = build_services(settings)
        app.state.settings = settings
        app.state.services = services
        try:
            yield
        finally:
            await services["feishu_client"].close()
            services["store"].close()

    app = FastAPI(title=APP_NAME, lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> Dict[str, bool]:
        return {"ok": True}

    @app.post("/feishu/events")
    async def feishu_events(request: Request) -> Dict[str, Any]:
        if not settings.feishu_enabled:
            raise HTTPException(status_code=503, detail="Feishu is disabled")
        if settings.feishu_event_mode != "webhook":
            raise HTTPException(
                status_code=503,
                detail="Feishu webhook endpoint is disabled in long_connection mode",
            )

        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="invalid payload")

        if payload.get("type") == "url_verification":
            if not verify_token(
                settings.feishu_verification_token, payload.get("token")
            ):
                raise HTTPException(status_code=403, detail="token mismatch")
            challenge = payload.get("challenge")
            if not isinstance(challenge, str):
                raise HTTPException(status_code=400, detail="missing challenge")
            return {"challenge": challenge}

        header = payload.get("header")
        if not isinstance(header, dict):
            return {"code": 0, "msg": "ignored"}

        if not verify_token(settings.feishu_verification_token, header.get("token")):
            raise HTTPException(status_code=403, detail="token mismatch")

        event_type = header.get("event_type")
        if event_type != "im.message.receive_v1":
            return {"code": 0, "msg": "ignored"}

        inbound = parse_inbound_message(payload)
        if inbound is None:
            return {"code": 0, "msg": "ignored"}

        task = asyncio.create_task(app.state.services["chat_service"].handle_message(inbound))
        task.add_done_callback(_log_task_exception)
        return {"code": 0, "msg": "success"}

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    log.info("Starting light-claw with Feishu event mode: %s", settings.feishu_event_mode)
    if settings.feishu_event_mode == "long_connection":
        asyncio.run(run_long_connection(settings))
        return
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


async def run_long_connection(settings: Settings) -> None:
    services = build_services(settings)
    loop = asyncio.get_running_loop()
    client = FeishuLongConnectionClient(
        settings=settings,
        chat_service=services["chat_service"],
        loop=loop,
    )
    try:
        await asyncio.to_thread(client.start)
    finally:
        await services["feishu_client"].close()
        services["store"].close()


def _log_task_exception(task: asyncio.Task[Any]) -> None:
    try:
        task.result()
    except Exception:
        log.exception("background task failed")
