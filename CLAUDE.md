# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

`light-claw` is a Python service that bridges Feishu (enterprise messaging) to local CLI coding agents (OpenAI Codex, Anthropic Claude Code). It runs long-lived agents on a single machine with persistent workspaces and session state in SQLite.

## Commands

```bash
# Install dependencies
uv sync

# Run the server
uv run light-claw

# Run all tests
uv run python -m pytest tests/

# Run a single test file
uv run python -m pytest tests/test_server.py

# Run a single test
uv run python -m pytest tests/test_server.py::ServerTest::test_health_endpoints_are_ready_for_local_process
```

There is no linter, formatter, or type checker configured — this is intentional.

## Architecture

**Inbound flow:** Feishu messages arrive via webhook (`POST /feishu/events`) or long-connection WebSocket. Both paths converge in `ChatService`, which either handles slash commands or forwards prompts to `TaskExecutor`.

**Single execution path:** `TaskExecutor` is the one place where all prompts run. It handles session persistence and CLI invocation.

**Multi-agent:** Each agent gets its own `AgentRuntime` (channel, registry, executor, chat service). All agents share one `StateStore` and one `WorkspaceManager`.

**Key modules:**
- `server.py` — FastAPI app, health endpoints, entry point
- `runtime_services.py` — wires all services together, owns lifecycle
- `task_executor.py` — core prompt execution path
- `store.py` — all SQLite operations (schema, migrations, CRUD)
- `config.py` — Settings/AgentSettings dataclasses, env loading
- `communication/feishu.py` — Feishu HTTP API + WebSocket long-connection
- `runtime/codex_cli.py` — Codex subprocess runner
- `runtime/claude_code.py` — Claude Code subprocess runner
- `runtime/registry.py` — CliRuntime protocol and provider registry
- `chat.py` — message dedup, per-conversation locking, dispatch
- `chat_commands.py` — slash command handling
- `commands.py` — command parsing and help text
- `workspaces.py` — workspace creation and layout management

**Data:** SQLite at `.data/light-claw.db`, workspaces at `.data/workspaces/<agent>/`.

## Engineering Philosophy (from AGENTS.md)

This codebase deliberately optimizes for fewer modules, fewer dependencies, fewer abstractions, fewer files, and fewer moving parts. Priority order: correctness > readability > simplicity > maintainability > performance.

**Do:** Write direct/explicit code, keep logic close to where it's used, prefer stdlib over dependencies, make small local changes.

**Don't:** Introduce DI containers, plugin systems, event buses, deep inheritance, generic factories, manager/service wrappers without strong justification, or split code into many tiny files. A small amount of duplication is preferable to a hard-to-follow abstraction. Only add dependencies when they provide substantial value not achievable with stdlib or existing deps.
