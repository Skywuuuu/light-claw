from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .storage import append_daily_memory_note, read_memory_file, search_memory_sources

_SERVER_NAME = "light-claw-memory"
_SERVER_VERSION = "0.1.0"


def build_memory_tool_definitions() -> list[dict[str, Any]]:
    """Return the MCP tool metadata exposed by the memory server."""
    return [
        {
            "name": "memory_append",
            "description": "Append one dated memory note to memory/daily/YYYY-MM-DD.md.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "entry_date": {"type": "string"},
                },
                "required": ["content"],
                "additionalProperties": False,
            },
        },
        {
            "name": "memory_search",
            "description": "Search AGENTS.md, task memory, and daily memory across global/group/memory scopes.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
        {
            "name": "memory_get",
            "description": "Read one specific memory file or a selected line range.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "end_line": {"type": "integer"},
                },
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    ]


def call_memory_tool(
    workspace_dir: Path,
    tool_name: str,
    arguments: dict[str, Any] | None,
) -> dict[str, Any]:
    """Execute one memory MCP tool call.

    Args:
        workspace_dir: Workspace root directory used by the memory tool.
        tool_name: Tool name requested by the MCP client.
        arguments: Tool arguments from the MCP request.
    """
    args = arguments or {}
    if tool_name == "memory_append":
        content = str(args.get("content", "")).strip()
        if not content:
            raise ValueError("memory_append requires non-empty content")
        path = append_daily_memory_note(
            workspace_dir,
            content,
            entry_date=args.get("entry_date"),
        )
        return _tool_text_result(f"Appended daily memory to `{path}`.")
    if tool_name == "memory_search":
        hits = search_memory_sources(
            workspace_dir,
            str(args.get("query", "")),
            limit=int(args.get("limit", 20)),
        )
        if not hits:
            return _tool_text_result("No memory hits found.")
        rendered = "\n".join(
            f"- [{hit.scope}] {hit.path}:{hit.line_number} {hit.preview}" for hit in hits
        )
        return _tool_text_result(rendered)
    if tool_name == "memory_get":
        result = read_memory_file(
            workspace_dir,
            str(args.get("path", "")),
            start_line=_optional_int(args.get("start_line")),
            end_line=_optional_int(args.get("end_line")),
        )
        rendered = "\n".join(
            [
                f"Path: {result.path}",
                f"Scope: {result.scope}",
                f"Lines: {result.start_line}-{result.end_line}",
                "",
                result.content,
            ]
        ).strip()
        return _tool_text_result(rendered)
    raise ValueError(f"Unknown memory tool: {tool_name}")


def serve_stdio(workspace_dir: Path) -> None:
    """Serve the light-claw memory MCP protocol over stdio.

    Args:
        workspace_dir: Workspace root directory exposed to the memory tools.
    """
    while True:
        request = _read_protocol_message(sys.stdin.buffer)
        if request is None:
            return
        if "method" not in request:
            continue
        response = _handle_protocol_message(workspace_dir, request)
        if response is not None:
            _write_protocol_message(sys.stdout.buffer, response)


def _handle_protocol_message(
    workspace_dir: Path,
    request: dict[str, Any],
) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        protocol_version = request.get("params", {}).get("protocolVersion", "2024-11-05")
        return _success_response(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            },
        )
    if method == "ping":
        return _success_response(request_id, {})
    if method == "tools/list":
        return _success_response(request_id, {"tools": build_memory_tool_definitions()})
    if method == "tools/call":
        try:
            params = request.get("params") or {}
            result = call_memory_tool(
                workspace_dir,
                str(params.get("name", "")),
                params.get("arguments") or {},
            )
            return _success_response(request_id, result)
        except Exception as exc:
            return _success_response(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
    return _error_response(request_id, -32601, f"Method not found: {method}")


def _read_protocol_message(stream) -> dict[str, Any] | None:
    headers: dict[str, str] = {}
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        decoded = line.decode("utf-8").strip()
        if ":" not in decoded:
            continue
        key, value = decoded.split(":", 1)
        headers[key.strip().lower()] = value.strip()
    content_length = int(headers.get("content-length", "0"))
    if content_length <= 0:
        return None
    body = stream.read(content_length)
    return json.loads(body.decode("utf-8"))


def _write_protocol_message(stream, message: dict[str, Any]) -> None:
    body = json.dumps(message).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("utf-8"))
    stream.write(body)
    stream.flush()


def _success_response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_text_result(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def main() -> None:
    """Run the light-claw memory MCP server on stdio."""
    workspace_value = os.environ.get("LIGHT_CLAW_MEMORY_WORKSPACE")
    if not workspace_value:
        raise SystemExit("LIGHT_CLAW_MEMORY_WORKSPACE is required")
    serve_stdio(Path(workspace_value).resolve())


if __name__ == "__main__":
    main()
