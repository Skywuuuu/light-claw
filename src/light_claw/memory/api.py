from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..runtime_services import RuntimeServices
from .storage import (
    append_daily_memory_note,
    list_memory_sources,
    read_memory_file,
    search_memory_sources,
    write_memory_file,
)


class MemoryFileUpdateRequest(BaseModel):
    path: str
    content: str


class DailyMemoryAppendRequest(BaseModel):
    content: str
    entry_date: Optional[str] = None


def create_memory_router() -> APIRouter:
    """Create the HTTP API router used to inspect and edit workspace memory."""
    router = APIRouter(prefix='/api/memory', tags=['memory'])

    def _read_file(
        *,
        workspace_path,
        path: str,
        start_line: Optional[int],
        end_line: Optional[int],
    ) -> dict[str, object]:
        try:
            result = read_memory_file(
                workspace_path,
                path,
                start_line=start_line,
                end_line=end_line,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail='memory file not found')
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return result.__dict__

    def _write_file(*, workspace_path, path: str, content: str) -> dict[str, str]:
        try:
            written_path = write_memory_file(workspace_path, path, content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {'path': written_path}

    def _append_daily(*, workspace_path, content: str, entry_date: Optional[str]) -> dict[str, str]:
        written_path = append_daily_memory_note(
            workspace_path,
            content,
            entry_date=entry_date,
        )
        return {'path': written_path}

    @router.get('/{agent_id}/sources')
    async def list_sources(
        agent_id: str,
        services: RuntimeServices = Depends(_get_services),
    ) -> dict[str, object]:
        workspace = _require_workspace(services, agent_id)
        return {'sources': [source.__dict__ for source in list_memory_sources(workspace.path)]}

    @router.get('/{agent_id}/search')
    async def search_sources(
        agent_id: str,
        query: str,
        limit: int = 20,
        services: RuntimeServices = Depends(_get_services),
    ) -> dict[str, object]:
        workspace = _require_workspace(services, agent_id)
        return {
            'hits': [
                hit.__dict__
                for hit in search_memory_sources(workspace.path, query, limit=limit)
            ]
        }

    @router.get('/{agent_id}/file')
    async def get_memory_file(
        agent_id: str,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        services: RuntimeServices = Depends(_get_services),
    ) -> dict[str, object]:
        workspace = _require_workspace(services, agent_id)
        return _read_file(
            workspace_path=workspace.path,
            path=path,
            start_line=start_line,
            end_line=end_line,
        )

    @router.get('/{agent_id}/get')
    async def get_memory_alias(
        agent_id: str,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        services: RuntimeServices = Depends(_get_services),
    ) -> dict[str, object]:
        workspace = _require_workspace(services, agent_id)
        return _read_file(
            workspace_path=workspace.path,
            path=path,
            start_line=start_line,
            end_line=end_line,
        )

    @router.put('/{agent_id}/file')
    async def update_memory_file(
        agent_id: str,
        request: MemoryFileUpdateRequest,
        services: RuntimeServices = Depends(_get_services),
    ) -> dict[str, str]:
        workspace = _require_workspace(services, agent_id)
        return _write_file(
            workspace_path=workspace.path,
            path=request.path,
            content=request.content,
        )

    @router.post('/{agent_id}/append')
    async def append_daily_memory(
        agent_id: str,
        request: DailyMemoryAppendRequest,
        services: RuntimeServices = Depends(_get_services),
    ) -> dict[str, str]:
        workspace = _require_workspace(services, agent_id)
        return _append_daily(
            workspace_path=workspace.path,
            content=request.content,
            entry_date=request.entry_date,
        )

    @router.post('/{agent_id}/append-daily')
    async def append_daily_memory_legacy(
        agent_id: str,
        request: DailyMemoryAppendRequest,
        services: RuntimeServices = Depends(_get_services),
    ) -> dict[str, str]:
        workspace = _require_workspace(services, agent_id)
        return _append_daily(
            workspace_path=workspace.path,
            content=request.content,
            entry_date=request.entry_date,
        )

    return router


def _get_services(request: Request) -> RuntimeServices:
    return request.app.state.services


def _require_workspace(services: RuntimeServices, agent_id: str):
    workspace = services.store.get_agent_workspace(agent_id)
    if workspace is None:
        raise HTTPException(status_code=404, detail='workspace not found')
    return workspace
