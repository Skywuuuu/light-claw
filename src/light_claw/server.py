from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import APP_NAME, Settings
from .memory.api import create_memory_router
from .runtime_services import (
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
    app.include_router(create_memory_router())

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
    async def healthz_details() -> dict[str, object]:
        return app.state.services.health.snapshot(
            store_ok=app.state.services.store.ping()
        )

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.from_env()
    log.info(
        "Starting light-claw mode=%s agents=%s",
        settings.feishu_event_mode,
        ",".join(agent.agent_id for agent in settings.agents),
    )
    if settings.feishu_enabled:
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
