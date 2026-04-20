from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

sys.modules.setdefault("lark_oapi", types.SimpleNamespace())

from light_claw.chat_commands import ChatCommandHandler
from light_claw.commands import Command
from light_claw.communication.messages import InboundMessage, ReplyTarget
from light_claw.config import AgentSettings, Settings
from light_claw.models import CliProviderInfo, WorkspaceRecord
from light_claw.runtime import CliRuntimeRegistry
from light_claw.runtime.claude_code import ClaudeCodeRuntime
from light_claw.runtime.codex_cli import CodexCliRuntime
from light_claw.store import StateStore
from light_claw.workspaces import WorkspaceManager


class _FakeCommunicationChannel:
    async def send_text(self, target, content):
        pass


def _settings(tmp_dir: str) -> Settings:
    return Settings(
        base_dir=Path(tmp_dir), host="127.0.0.1", port=8000,
        data_dir=Path(tmp_dir) / ".data",
        database_path=Path(tmp_dir) / ".data" / "state.db",
        workspaces_dir=Path(tmp_dir) / ".data" / "workspaces",
        claude_bin="claude", claude_model=None,
        claude_permission_mode="bypassPermissions", claude_add_dirs=[],
        codex_bin="codex", codex_model=None, codex_search=False,
        codex_sandbox="full-auto", codex_timeout_min_seconds=180,
        codex_timeout_max_seconds=900, codex_timeout_per_char_ms=80,
        codex_stall_timeout_seconds=120, codex_add_dirs=[],
        status_heartbeat_enabled=False, status_heartbeat_seconds=3600,
        inbound_message_ttl_seconds=60, default_cli_provider="codex",
        feishu_enabled=False, feishu_event_mode="webhook",
        feishu_app_id=None, feishu_app_secret=None,
        feishu_verification_token=None, allow_from="*",
        default_workspace_name="default", agents=(),
    )


def _agent(
    mcp_config_path: Path | None = None,
    default_cli_provider: str = "codex",
) -> AgentSettings:
    return AgentSettings(
        agent_id="a", name="A", feishu_app_id=None, feishu_app_secret=None,
        feishu_verification_token=None, allow_from="*",
        default_workspace_name="default", default_cli_provider=default_cli_provider,
        codex_model=None, codex_search=False, codex_sandbox="full-auto",
        skills_path=None, mcp_config_path=mcp_config_path,
    )


def _msg() -> InboundMessage:
    return InboundMessage(
        agent_id="a", bot_app_id="bot", owner_id="ou_1",
        conversation_id="c1", message_id="m1", message_type="text",
        content="/skills", reply_target=ReplyTarget("ou_1", "open_id"),
    )


def _registry(
    claude_config_dir: Path, codex_config_dir: Path,
) -> CliRuntimeRegistry:
    return CliRuntimeRegistry(
        providers=[
            CliProviderInfo("claude-code", "Claude Code", "test", True),
            CliProviderInfo("codex", "Codex", "test", True),
        ],
        runtimes={
            "claude-code": ClaudeCodeRuntime(config_dir=claude_config_dir),
            "codex": CodexCliRuntime(config_dir=codex_config_dir),
        },
    )


def _setup_workspace(store, tmp_dir, cli_provider):
    wp = Path(tmp_dir) / "workspaces" / "a"
    wp.mkdir(parents=True, exist_ok=True)
    store.create_workspace(WorkspaceRecord(
        agent_id="a", owner_id="ou_1", workspace_id="default",
        name="Default", path=wp, cli_provider=cli_provider,
        created_at=0.0, updated_at=0.0,
    ))


class SkillsCommandTest(unittest.IsolatedAsyncioTestCase):
    async def test_shows_claude_code_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_dir = Path(tmp) / ".claude"
            skill_dir = claude_dir / "skills" / "pdf"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: pdf\ndescription: PDF tools\n---\n", encoding="utf-8"
            )
            store = StateStore(Path(tmp) / "state.db")
            _setup_workspace(store, tmp, "claude-code")
            handler = ChatCommandHandler(
                settings=_settings(tmp), agent=_agent(default_cli_provider="claude-code"), store=store,
                workspace_manager=WorkspaceManager(
                    Path(tmp) / "workspaces",
                ),
                cli_registry=_registry(claude_dir, Path(tmp) / ".codex"),
                communication_channel=_FakeCommunicationChannel(),
            )
            result = await handler.handle(_msg(), Command(kind="skills"))
            self.assertIn("Claude Code", result)
            self.assertIn("pdf", result)
            self.assertIn("PDF tools", result)
            store.close()

    async def test_shows_codex_plugin_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_dir = Path(tmp) / ".codex"
            plugin_dir = codex_dir / "plugins" / "cache" / "mp" / "gh" / "abc"
            skill_dir = plugin_dir / "skills" / "fix-ci"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: fix-ci\ndescription: Fix CI\n---\n", encoding="utf-8"
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "github", "version": "0.1.0"}),
                encoding="utf-8",
            )
            (codex_dir / "config.toml").write_text(
                '[plugins."gh@mp"]\nenabled = true\n', encoding="utf-8"
            )
            store = StateStore(Path(tmp) / "state.db")
            _setup_workspace(store, tmp, "codex")
            handler = ChatCommandHandler(
                settings=_settings(tmp), agent=_agent(), store=store,
                workspace_manager=WorkspaceManager(
                    Path(tmp) / "workspaces",
                ),
                cli_registry=_registry(Path(tmp) / ".claude", codex_dir),
                communication_channel=_FakeCommunicationChannel(),
            )
            result = await handler.handle(_msg(), Command(kind="skills"))
            self.assertIn("Codex", result)
            self.assertIn("fix-ci", result)
            store.close()

    async def test_shows_mcp_servers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mcp_file = Path(tmp) / "mcp.json"
            mcp_file.write_text(
                '{"mcpServers": {"zotero": {}, "github": {}}}', encoding="utf-8"
            )
            store = StateStore(Path(tmp) / "state.db")
            _setup_workspace(store, tmp, "codex")
            handler = ChatCommandHandler(
                settings=_settings(tmp),
                agent=_agent(mcp_config_path=mcp_file),
                store=store,
                workspace_manager=WorkspaceManager(
                    Path(tmp) / "workspaces",
                ),
                cli_registry=_registry(
                    Path(tmp) / ".claude", Path(tmp) / ".codex"
                ),
                communication_channel=_FakeCommunicationChannel(),
            )
            result = await handler.handle(_msg(), Command(kind="skills"))
            self.assertIn("Configured MCP tools:", result)
            self.assertIn("github", result)
            self.assertIn("zotero", result)
            store.close()

    async def test_shows_no_skills_message_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(Path(tmp) / "state.db")
            _setup_workspace(store, tmp, "codex")
            handler = ChatCommandHandler(
                settings=_settings(tmp), agent=_agent(), store=store,
                workspace_manager=WorkspaceManager(
                    Path(tmp) / "workspaces",
                ),
                cli_registry=_registry(
                    Path(tmp) / ".claude", Path(tmp) / ".codex"
                ),
                communication_channel=_FakeCommunicationChannel(),
            )
            result = await handler.handle(_msg(), Command(kind="skills"))
            self.assertIn("No skills or MCP tools found", result)
            store.close()


if __name__ == "__main__":
    unittest.main()
