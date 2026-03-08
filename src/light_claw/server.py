from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .archive import WorkspaceArchiveService
from .chat import ChatObserver, ChatService
from .cli_runners import CliRunnerRegistry
from .config import AgentSettings, APP_NAME, Settings
from .feishu import FeishuClient, parse_inbound_message, verify_token
from .feishu_long_connection import FeishuLongConnectionClient
from .store import StateStore
from .workspaces import WorkspaceManager


log = logging.getLogger("light_claw.server")


@dataclass
class AgentRuntime:
    agent: AgentSettings
    cli_registry: CliRunnerRegistry
    feishu_client: FeishuClient
    chat_service: ChatService


@dataclass
class RuntimeServices:
    settings: Settings
    store: StateStore
    workspace_manager: WorkspaceManager
    archive_service: WorkspaceArchiveService | None
    health: "RuntimeHealth"
    agent_runtimes: dict[str, AgentRuntime]


class RuntimeHealth(ChatObserver):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.started_at = time.time()
        self.archive_running = False
        self.archive_last_success_at: float | None = None
        self.archive_last_error: str | None = None
        self.background_error_count = 0
        self.message_counts = {
            "received": 0,
            "completed": 0,
            "failed": 0,
        }
        self.outcome_counts: dict[str, int] = {}
        self.agent_states = {
            agent.agent_id: {
                "app_id": agent.feishu_app_id,
                "connected": False,
                "last_event_at": None,
            }
            for agent in settings.agents
        }

    def mark_archive_started(self) -> None:
        self.archive_running = True

    def mark_archive_stopped(self) -> None:
        self.archive_running = False

    def mark_archive_synced(self) -> None:
        self.archive_last_success_at = time.time()
        self.archive_last_error = None

    def mark_archive_error(self, exc: Exception) -> None:
        self.archive_last_error = str(exc)

    def mark_agent_connection(self, agent_id: str, connected: bool) -> None:
        state = self.agent_states.setdefault(
            agent_id,
            {"app_id": None, "connected": False, "last_event_at": None},
        )
        state["connected"] = connected

    def mark_agent_event(self, agent_id: str) -> None:
        state = self.agent_states.setdefault(
            agent_id,
            {"app_id": None, "connected": False, "last_event_at": None},
        )
        state["last_event_at"] = time.time()

    def mark_background_error(self) -> None:
        self.background_error_count += 1

    def on_message_received(self, agent_id: str) -> None:
        self.message_counts["received"] += 1
        self.mark_agent_event(agent_id)

    def on_message_completed(
        self,
        agent_id: str,
        *,
        outcome: str,
        latency_ms: int,
    ) -> None:
        self.message_counts["completed"] += 1
        self.outcome_counts[outcome] = self.outcome_counts.get(outcome, 0) + 1
        log.info(
            "message completed agent=%s outcome=%s latency_ms=%s",
            agent_id,
            outcome,
            latency_ms,
        )

    def on_message_failed(self, agent_id: str, *, latency_ms: int) -> None:
        self.message_counts["failed"] += 1
        log.exception("message failed agent=%s latency_ms=%s", agent_id, latency_ms)

    def snapshot(self, *, store_ok: bool) -> dict[str, Any]:
        if self.settings.feishu_enabled and self.settings.feishu_event_mode == "long_connection":
            agents_ready = all(
                bool(state["connected"]) for state in self.agent_states.values()
            )
        else:
            agents_ready = True
        ready = store_ok and agents_ready and (
            (not self.settings.archive_enabled) or self.archive_running
        )
        return {
            "app": APP_NAME,
            "started_at": self.started_at,
            "uptime_seconds": int(time.time() - self.started_at),
            "event_mode": self.settings.feishu_event_mode,
            "store_ok": store_ok,
            "archive": {
                "enabled": self.settings.archive_enabled,
                "running": self.archive_running,
                "last_success_at": self.archive_last_success_at,
                "last_error": self.archive_last_error,
            },
            "agents": self.agent_states,
            "messages": self.message_counts,
            "outcomes": self.outcome_counts,
            "background_error_count": self.background_error_count,
            "ready": ready,
        }


def build_services(settings: Settings) -> RuntimeServices:
    store = StateStore(settings.database_path)
    store.prune_inbound_messages(settings.inbound_message_ttl_seconds)
    workspace_manager = WorkspaceManager(settings.workspaces_dir)
    health = RuntimeHealth(settings)
    archive_service = None
    if settings.archive_enabled:
        archive_service = WorkspaceArchiveService(
            store=store,
            archive_root=settings.archive_dir,
            interval_seconds=settings.archive_interval_seconds,
            inbound_message_ttl_seconds=settings.inbound_message_ttl_seconds,
            on_sync_success=health.mark_archive_synced,
            on_sync_error=health.mark_archive_error,
        )

    agent_runtimes: dict[str, AgentRuntime] = {}
    for agent in settings.agents:
        cli_registry = CliRunnerRegistry.from_settings(settings, agent)
        feishu_client = FeishuClient(
            app_id=agent.feishu_app_id or "",
            app_secret=agent.feishu_app_secret or "",
        )
        chat_service = ChatService(
            settings=settings,
            agent=agent,
            store=store,
            workspace_manager=workspace_manager,
            cli_registry=cli_registry,
            feishu_client=feishu_client,
            observer=health,
        )
        agent_runtimes[agent.agent_id] = AgentRuntime(
            agent=agent,
            cli_registry=cli_registry,
            feishu_client=feishu_client,
            chat_service=chat_service,
        )

    return RuntimeServices(
        settings=settings,
        store=store,
        workspace_manager=workspace_manager,
        archive_service=archive_service,
        health=health,
        agent_runtimes=agent_runtimes,
    )


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
            if manage_lifecycle and active_services.archive_service is not None:
                await active_services.archive_service.start()
                active_services.health.mark_archive_started()
            yield
        finally:
            if manage_lifecycle:
                await _shutdown_services(active_services)

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
        if services.archive_service is not None:
            await services.archive_service.start()
            services.health.mark_archive_started()

        loop = asyncio.get_running_loop()
        for runtime in services.agent_runtimes.values():
            client = FeishuLongConnectionClient(
                agent_id=runtime.agent.agent_id,
                app_id=runtime.agent.feishu_app_id or "",
                app_secret=runtime.agent.feishu_app_secret or "",
                chat_service=runtime.chat_service,
                loop=loop,
                on_running_change=services.health.mark_agent_connection,
            )
            long_connection_tasks.append(asyncio.create_task(asyncio.to_thread(client.start)))

        await asyncio.gather(probe_task, *long_connection_tasks)
    finally:
        probe_server.should_exit = True
        await asyncio.gather(probe_task, return_exceptions=True)
        for task in long_connection_tasks:
            task.cancel()
        await asyncio.gather(*long_connection_tasks, return_exceptions=True)
        await _shutdown_services(services)


async def _shutdown_services(services: RuntimeServices) -> None:
    if services.archive_service is not None:
        await services.archive_service.stop()
        services.health.mark_archive_stopped()
    for runtime in services.agent_runtimes.values():
        await runtime.feishu_client.close()
    services.store.close()


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
