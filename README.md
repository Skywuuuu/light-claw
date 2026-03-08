# light-claw

`light-claw` is a Python MVP for running a long-lived coding agent behind Feishu.

- Feishu webhook ingress + outbound reply API
- Persistent workspace/session state in SQLite
- One isolated workspace per agent/work context
- Per-workspace CLI provider selection
- Each workspace is bootstrapped with `AGENTS.md` and `memory/`
- CLI conversations resume on the same Feishu conversation until `/reset` or workspace switch

## What this MVP supports

- Feishu event webhook mode
- Text conversations through the selected CLI provider
- Pluggable CLI provider registry, with Codex implemented and reserved slots for Claude Code and custom CLIs
- Workspace commands from Feishu:
  - `/cli list`
  - `/cli current`
  - `/cli use <provider>`
  - `/help`
  - `/workspace list`
  - `/workspace create <name>`
  - `/workspace use <id|index>`
  - `/workspace current`
  - `/reset`

## Project layout

```text
src/light_claw/
  __init__.py
  __main__.py
  chat.py
  cli_runners.py
  codex_runner.py
  commands.py
  config.py
  feishu.py
  models.py
  server.py
  store.py
  workspaces.py
```

Runtime data is stored under `.data/` by default:

```text
.data/
  light-claw.db
  workspaces/
    <user>/
      <workspace-id>/
        AGENTS.md
        README.md
        memory/
```

## Setup

1. Install dependencies with `uv`.

```bash
uv sync
```

2. Copy the env template.

```bash
cp .env.example .env
```

3. Fill in your Feishu app credentials and choose a Feishu event mode.

4. Ensure `codex` is installed and can run non-interactively on this machine.

5. Start the server.

```bash
uv run light-claw
```

Or:

```bash
uv run uvicorn light_claw.server:create_app --factory --host 0.0.0.0 --port 8000
```

## Feishu app configuration

This MVP supports both Feishu delivery modes through `FEISHU_EVENT_MODE`.

### Webhook mode

Use `FEISHU_EVENT_MODE=webhook` when you want Feishu to call your HTTP server.

- Event subscription URL: `POST /feishu/events`
- Health check: `GET /healthz`
- Enable at least `im.message.receive_v1`
- Set `FEISHU_VERIFICATION_TOKEN` in both Feishu and `.env`
- Start with `uv run light-claw` or `uv run uvicorn light_claw.server:create_app --factory --host 0.0.0.0 --port 8000`

### Long-connection mode

Use `FEISHU_EVENT_MODE=long_connection` when you want the process to keep a websocket connection to Feishu.

- `FEISHU_VERIFICATION_TOKEN` is not required
- Start with `uv run light-claw`
- Make sure the process is already running before saving the "use long connection" setting in the Feishu console
- Enable at least `im.message.receive_v1`

## Workspace behavior

The first user message automatically gets a default workspace if none exists.

Each workspace contains:

- `AGENTS.md`
- `README.md`
- `memory/identity.md`
- `memory/profile.md`
- `memory/preferences.md`
- `memory/projects.md`
- `memory/decisions.md`
- `memory/open_loops.md`
- `memory/daily/README.md`

The selected CLI runs inside the selected workspace, so the workspace instructions and memory files are part of its local context.

Each workspace also stores a selected CLI provider. The current implementation ships with:

- `codex`: implemented
- `claude-code`: reserved provider slot
- `custom`: reserved provider slot

That means the execution path is no longer hardcoded to Codex. Adding Claude Code later is now an adapter task instead of a service-wide refactor.

## Notes

- This is an MVP. It currently focuses on text messages, webhook delivery, and long-connection delivery.
- Feishu rich media and card actions can be layered on later.
- The gateway reference repo already shows the broader direction for reminders, richer transports, browser MCP, and skill management.
- The runtime prefers `LIGHT_CLAW_SANDBOX`; it still accepts legacy `CODEX_CLAW_SANDBOX` and `CODEX_SANDBOX`, and maps host sandbox values like `workspace-write` to a safe Codex CLI mode.
- The default SQLite path is `.data/light-claw.db`; if `.data/codex-claw.db` already exists, the runtime keeps using it automatically.
- `DEFAULT_CLI_PROVIDER` controls the provider used for newly created workspaces.
- `uv sync` creates and manages the project's virtual environment automatically. Use `uv run ...` for local commands.
