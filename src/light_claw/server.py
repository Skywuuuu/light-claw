from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .communication.feishu import (
    parse_inbound_message,
    verify_token,
)
from .config import AgentSettings, APP_NAME, Settings
from .runtime_services import (
    RuntimeHealth,
    RuntimeServices,
    build_services,
    shutdown_services,
    start_managed_services,
)


log = logging.getLogger("light_claw.server")


def create_app(
    settings: Optional[Settings] = None,
    services: RuntimeServices | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_services = services or build_services(settings)
        app.state.settings = settings
        app.state.services = active_services
        manage_lifecycle = services is None
        try:
            if manage_lifecycle:
                await start_managed_services(active_services)
            yield
        finally:
            if manage_lifecycle:
                await shutdown_services(active_services)

    app = FastAPI(title=APP_NAME, lifespan=lifespan)

    @app.get("/livez")
    async def livez() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        details = app.state.services.health.snapshot(
            store_ok=app.state.services.store.ping()
        )
        status_code = 200 if details["ready"] else 503
        return JSONResponse(status_code=status_code, content=details)

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        details = app.state.services.health.snapshot(
            store_ok=app.state.services.store.ping()
        )
        return {"ok": bool(details["ready"])}

    @app.get("/healthz/details")
    async def healthz_details() -> dict[str, Any]:
        return app.state.services.health.snapshot(
            store_ok=app.state.services.store.ping()
        )

    @app.post("/feishu/events")
    async def feishu_events(request: Request) -> dict[str, Any]:
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
            agent = _resolve_verification_agent(settings, payload)
            if agent is None:
                raise HTTPException(status_code=403, detail="token mismatch")
            challenge = payload.get("challenge")
            if not isinstance(challenge, str):
                raise HTTPException(status_code=400, detail="missing challenge")
            return {"challenge": challenge}

        header = payload.get("header")
        if not isinstance(header, dict):
            return {"code": 0, "msg": "ignored"}
        agent = _resolve_agent_from_header(settings, header)
        if agent is None:
            return {"code": 0, "msg": "ignored"}
        if not verify_token(agent.feishu_verification_token, header.get("token")):
            raise HTTPException(status_code=403, detail="token mismatch")

        event_type = header.get("event_type")
        if event_type != "im.message.receive_v1":
            return {"code": 0, "msg": "ignored"}

        inbound = parse_inbound_message(
            payload,
            agent_id=agent.agent_id,
            bot_app_id=agent.feishu_app_id or "",
        )
        if inbound is None:
            return {"code": 0, "msg": "ignored"}

        runtime = app.state.services.agent_runtimes[agent.agent_id]
        task = asyncio.create_task(runtime.chat_service.handle_message(inbound))
        task.add_done_callback(lambda current: _log_task_exception(current, app.state.services.health))
        return {"code": 0, "msg": "success"}

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    log.info(
        "Starting light-claw mode=%s agents=%s",
        settings.feishu_event_mode,
        ",".join(agent.agent_id for agent in settings.agents),
    )
    if settings.feishu_event_mode == "long_connection":
        asyncio.run(run_long_connection(settings))
        return
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


async def run_long_connection(settings: Settings) -> None:
    services = build_services(settings)
    probe_server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings, services=services),
            host=settings.host,
            port=settings.port,
            log_level="info",
        )
    )
    probe_task = asyncio.create_task(probe_server.serve())
    long_connection_tasks: list[asyncio.Task[None]] = []
    try:
        await start_managed_services(services)
        loop = asyncio.get_running_loop()
        for runtime in services.agent_runtimes.values():
            runtime.communication_channel.bind_inbound(
                chat_service=runtime.chat_service,
                loop=loop,
            )
            long_connection_tasks.append(
                asyncio.create_task(asyncio.to_thread(runtime.communication_channel.start))
            )

        await asyncio.gather(probe_task, *long_connection_tasks)
    finally:
        probe_server.should_exit = True
        await asyncio.gather(probe_task, return_exceptions=True)
        for runtime in services.agent_runtimes.values():
            runtime.communication_channel.stop()
        for task in long_connection_tasks:
            task.cancel()
        await asyncio.gather(*long_connection_tasks, return_exceptions=True)
        await shutdown_services(services)


def _resolve_verification_agent(
    settings: Settings,
    payload: dict[str, Any],
) -> AgentSettings | None:
    token = payload.get("token")
    for agent in settings.agents:
        if verify_token(agent.feishu_verification_token, token):
            return agent
    return None


def _resolve_agent_from_header(
    settings: Settings,
    header: dict[str, Any],
) -> AgentSettings | None:
    app_id = header.get("app_id")
    if not isinstance(app_id, str) or not app_id:
        return None
    return settings.get_agent_by_app_id(app_id)


def _log_task_exception(task: asyncio.Task[Any], health: RuntimeHealth) -> None:
    try:
        task.result()
    except Exception:
        health.mark_background_error()
        log.exception("background task failed")
