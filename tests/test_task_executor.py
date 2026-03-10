import tempfile
import unittest
from pathlib import Path

from light_claw.archive import WorkspaceArchiveService
from light_claw.chat import ChatService
from light_claw.config import AgentSettings, Settings
from light_claw.models import (
    CliProviderInfo,
    CliRunResult,
    FeishuInboundMessage,
    FeishuReplyTarget,
    WorkspaceRecord,
)
from light_claw.store import StateStore
from light_claw.task_executor import TaskExecutor


class _FakeRunner:
    def __init__(self, result: CliRunResult) -> None:
        self.result = result
        self.calls = []

    async def run(self, prompt, workspace_dir, session_id=None, on_activity=None):
        self.calls.append((prompt, workspace_dir, session_id))
        if on_activity is not None:
            on_activity()
        return self.result


class _FakeRegistry:
    def __init__(self, runner) -> None:
        self.runner = runner

    def get_runner(self, provider_id):
        return self.runner

    @staticmethod
    def default_provider_id(provider_id):
        return provider_id

    @staticmethod
    def validate_selectable(provider_id):
        normalized = provider_id.strip().lower()
        if normalized == "codex":
            return True, None
        return False, "Provider not available."

    @staticmethod
    def get_provider(provider_id):
        return CliProviderInfo(
            provider_id=provider_id,
            display_name=provider_id.title(),
            description="test provider",
            available=True,
        )

    @staticmethod
    def list_providers():
        return [
            CliProviderInfo(
                provider_id="codex",
                display_name="Codex",
                description="test provider",
                available=True,
            )
        ]


class _FakeFeishuClient:
    def __init__(self) -> None:
        self.messages = []

    async def send_text(self, target, content):
        self.messages.append((target.receive_id, target.receive_id_type, content))


class _FakeWorkspaceManager:
    @staticmethod
    def ensure_workspace_layout(
        workspace,
        *,
        agent_name,
        skills_path,
        mcp_config_path,
    ) -> None:
        return None


class TaskExecutorTest(unittest.IsolatedAsyncioTestCase):
    def _build_settings(self, tmp_dir: str) -> Settings:
        return Settings(
            base_dir=Path(tmp_dir),
            host="127.0.0.1",
            port=8000,
            data_dir=Path(tmp_dir) / ".data",
            database_path=Path(tmp_dir) / ".data" / "state.db",
            workspaces_dir=Path(tmp_dir) / ".data" / "workspaces",
            archive_enabled=False,
            archive_dir=Path(tmp_dir) / "archive",
            archive_interval_seconds=43200,
            codex_bin="codex",
            codex_model=None,
            codex_search=False,
            codex_sandbox="full-auto",
            codex_timeout_min_seconds=180,
            codex_timeout_max_seconds=900,
            codex_timeout_per_char_ms=80,
            codex_stall_timeout_seconds=120,
            task_heartbeat_enabled=True,
            task_heartbeat_interval_seconds=60,
            cron_enabled=True,
            cron_poll_interval_seconds=60,
            status_heartbeat_enabled=False,
            status_heartbeat_seconds=3600,
            inbound_message_ttl_seconds=60,
            default_cli_provider="codex",
            feishu_enabled=False,
            feishu_event_mode="webhook",
            feishu_app_id=None,
            feishu_app_secret=None,
            feishu_verification_token=None,
            allow_from="*",
            default_workspace_name="default",
            agents=(),
        )

    def _build_agent(self) -> AgentSettings:
        return AgentSettings(
            agent_id="agent-a",
            name="Agent A",
            feishu_app_id=None,
            feishu_app_secret=None,
            feishu_verification_token=None,
            allow_from="*",
            default_workspace_name="default",
            default_cli_provider="codex",
            codex_model=None,
            codex_search=False,
            codex_sandbox="full-auto",
            skills_path=None,
            mcp_config_path=None,
        )

    async def test_execute_prompt_persists_session_and_replies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            workspace = store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=Path(tmp_dir) / "default",
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            runner = _FakeRunner(CliRunResult(session_id="sess-1", answer="done", raw_output=""))
            feishu = _FakeFeishuClient()
            executor = TaskExecutor(
                settings=self._build_settings(tmp_dir),
                agent=self._build_agent(),
                store=store,
                cli_registry=_FakeRegistry(runner),
                feishu_client=feishu,
            )

            result = await executor.execute_prompt(
                workspace=workspace,
                prompt="Do work",
                conversation_id="conv_1",
                conversation_owner_id="ou_1",
                reply_target=FeishuReplyTarget("ou_1", "open_id"),
            )

            self.assertEqual(result.status, "succeeded")
            self.assertEqual(result.session_id, "sess-1")
            self.assertEqual(store.get_workspace_session_id("agent-a", "conv_1", "ou_1", "default"), "sess-1")
            self.assertEqual(feishu.messages[-1][2], "done")
            store.close()

    async def test_execute_workspace_task_records_run_and_reschedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = StateStore(Path(tmp_dir) / "state.db")
            store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=Path(tmp_dir) / "default",
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            task = store.create_workspace_task(
                "agent-a",
                "ou_1",
                "default",
                "Loop forever",
                notify_conversation_id="conv_1",
                notify_owner_id="ou_1",
                notify_receive_id="ou_1",
                notify_receive_id_type="open_id",
            )
            runner = _FakeRunner(CliRunResult(session_id="sess-2", answer="step complete", raw_output=""))
            executor = TaskExecutor(
                settings=self._build_settings(tmp_dir),
                agent=self._build_agent(),
                store=store,
                cli_registry=_FakeRegistry(runner),
                feishu_client=_FakeFeishuClient(),
            )

            result = await executor.execute_workspace_task(
                task,
                trigger_source="heartbeat",
                reschedule_seconds=120,
                announce_start=False,
                deliver_result=False,
            )

            self.assertIsNotNone(result)
            updated = store.get_workspace_task("agent-a", "ou_1", "default", task.task_id)
            self.assertEqual(updated.status, "running")
            self.assertIsNotNone(updated.next_run_at)
            self.assertEqual(updated.last_result_excerpt, "step complete")
            store.close()

    async def test_execute_prompt_injects_generic_observation_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_path = Path(tmp_dir) / "default"
            workspace_path.mkdir(parents=True, exist_ok=True)
            store = StateStore(Path(tmp_dir) / "state.db")
            workspace = store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=workspace_path,
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            runner = _FakeRunner(
                CliRunResult(session_id="sess-1", answer="done", raw_output="")
            )
            executor = TaskExecutor(
                settings=self._build_settings(tmp_dir),
                agent=self._build_agent(),
                store=store,
                cli_registry=_FakeRegistry(runner),
                feishu_client=_FakeFeishuClient(),
            )

            recorded = executor.record_observation(
                workspace=workspace,
                conversation_id="conv_1",
                conversation_owner_id="ou_1",
                kind="command_result",
                text="Workspace selected.\nResearch (research)",
                context_key="workspace_use:research",
            )

            self.assertTrue(recorded)

            await executor.execute_prompt(
                workspace=workspace,
                prompt="Continue",
                conversation_id="conv_1",
                conversation_owner_id="ou_1",
                deliver_result=False,
            )

            injected_prompt, resumed_dir, resumed_session = runner.calls[-1]
            self.assertEqual(resumed_dir.resolve(), workspace_path.resolve())
            self.assertIsNone(resumed_session)
            self.assertIn("Session observations:", injected_prompt)
            self.assertIn("Memory guidance:", injected_prompt)
            self.assertIn("command_result", injected_prompt)
            self.assertIn("Workspace selected.", injected_prompt)
            self.assertTrue(injected_prompt.rstrip().endswith("Continue"))

            runner.result = CliRunResult(
                session_id="sess-1",
                answer="follow-up done",
                raw_output="",
            )
            await executor.execute_prompt(
                workspace=workspace,
                prompt="Next turn",
                conversation_id="conv_1",
                conversation_owner_id="ou_1",
                deliver_result=False,
            )

            self.assertIn("Memory guidance:", runner.calls[-1][0])
            self.assertTrue(runner.calls[-1][0].rstrip().endswith("Next turn"))
            store.close()

    async def test_execute_prompt_injects_workspace_change_observation_on_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_path = Path(tmp_dir) / "default"
            workspace_path.mkdir(parents=True, exist_ok=True)
            store = StateStore(Path(tmp_dir) / "state.db")
            workspace = store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=workspace_path,
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            runner = _FakeRunner(
                CliRunResult(session_id="sess-1", answer="done", raw_output="")
            )
            executor = TaskExecutor(
                settings=self._build_settings(tmp_dir),
                agent=self._build_agent(),
                store=store,
                cli_registry=_FakeRegistry(runner),
                feishu_client=_FakeFeishuClient(),
            )

            await executor.execute_prompt(
                workspace=workspace,
                prompt="First turn",
                conversation_id="conv_1",
                conversation_owner_id="ou_1",
                deliver_result=False,
            )
            self.assertIn("Memory guidance:", runner.calls[0][0])
            self.assertTrue(runner.calls[0][0].rstrip().endswith("First turn"))

            (workspace_path / "IMPROVEMENT_RESEARCH.md").write_text(
                "external observation\nsecond line\n",
                encoding="utf-8",
            )
            runner.result = CliRunResult(
                session_id="sess-1",
                answer="follow-up done",
                raw_output="",
            )

            await executor.execute_prompt(
                workspace=workspace,
                prompt="Continue",
                conversation_id="conv_1",
                conversation_owner_id="ou_1",
                deliver_result=False,
            )

            resumed_prompt, resumed_dir, resumed_session = runner.calls[-1]
            self.assertEqual(resumed_dir.resolve(), workspace_path.resolve())
            self.assertEqual(resumed_session, "sess-1")
            self.assertIn("Session observations:", resumed_prompt)
            self.assertIn("Memory guidance:", resumed_prompt)
            self.assertIn("workspace_change", resumed_prompt)
            self.assertIn("Added: IMPROVEMENT_RESEARCH.md", resumed_prompt)
            self.assertIn("external observation", resumed_prompt)
            self.assertTrue(resumed_prompt.rstrip().endswith("Continue"))
            store.close()

    async def test_execute_workspace_task_records_progress_and_cron_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_path = Path(tmp_dir) / "default"
            workspace_path.mkdir(parents=True, exist_ok=True)
            store = StateStore(Path(tmp_dir) / "state.db")
            store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=workspace_path,
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            task = store.create_workspace_task(
                "agent-a",
                "ou_1",
                "default",
                "Research the next improvement",
                notify_conversation_id="conv_1",
                notify_owner_id="ou_1",
                notify_receive_id="ou_1",
                notify_receive_id_type="open_id",
            )
            runner = _FakeRunner(
                CliRunResult(session_id="sess-2", answer="Step 1 complete", raw_output="")
            )
            executor = TaskExecutor(
                settings=self._build_settings(tmp_dir),
                agent=self._build_agent(),
                store=store,
                cli_registry=_FakeRegistry(runner),
                feishu_client=_FakeFeishuClient(),
            )

            result = await executor.execute_workspace_task(
                task,
                trigger_source="cron",
                announce_start=False,
                deliver_result=False,
            )

            self.assertIsNotNone(result)
            cron_prompt = runner.calls[-1][0]
            self.assertIn("Scheduled task guidance:", cron_prompt)
            self.assertIn("memory/tasks/{}.md".format(task.task_id), cron_prompt)
            self.assertIn("lightweight, simple, and easy to understand", cron_prompt)
            progress_path = workspace_path / "memory" / "tasks" / "{}.md".format(task.task_id)
            self.assertTrue(progress_path.exists())
            self.assertIn("Step 1 complete", progress_path.read_text(encoding="utf-8"))
            store.close()

    async def test_chat_command_observation_is_injected_on_next_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_path = Path(tmp_dir) / "default"
            workspace_path.mkdir(parents=True, exist_ok=True)
            store = StateStore(Path(tmp_dir) / "state.db")
            workspace = store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=workspace_path,
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            runner = _FakeRunner(
                CliRunResult(session_id="sess-1", answer="done", raw_output="")
            )
            registry = _FakeRegistry(runner)
            feishu = _FakeFeishuClient()
            executor = TaskExecutor(
                settings=self._build_settings(tmp_dir),
                agent=self._build_agent(),
                store=store,
                cli_registry=registry,
                feishu_client=feishu,
            )
            chat = ChatService(
                settings=self._build_settings(tmp_dir),
                agent=self._build_agent(),
                store=store,
                workspace_manager=_FakeWorkspaceManager(),
                cli_registry=registry,
                feishu_client=feishu,
                task_executor=executor,
            )

            await chat.handle_message(
                FeishuInboundMessage(
                    agent_id="agent-a",
                    bot_app_id="bot-app",
                    owner_id="ou_1",
                    conversation_id="conv_1",
                    message_id="msg-1",
                    message_type="text",
                    content="/cli use codex",
                    reply_target=FeishuReplyTarget("ou_1", "open_id"),
                )
            )

            self.assertIn("CLI provider updated.", feishu.messages[-1][2])

            await chat.handle_message(
                FeishuInboundMessage(
                    agent_id="agent-a",
                    bot_app_id="bot-app",
                    owner_id="ou_1",
                    conversation_id="conv_1",
                    message_id="msg-2",
                    message_type="text",
                    content="Continue",
                    reply_target=FeishuReplyTarget("ou_1", "open_id"),
                )
            )

            injected_prompt, resumed_dir, resumed_session = runner.calls[-1]
            self.assertEqual(resumed_dir.resolve(), workspace.path.resolve())
            self.assertIsNone(resumed_session)
            self.assertIn("Session observations:", injected_prompt)
            self.assertIn("Memory guidance:", injected_prompt)
            self.assertIn("command_result", injected_prompt)
            self.assertIn("CLI provider updated.", injected_prompt)
            self.assertTrue(injected_prompt.rstrip().endswith("Continue"))
            store.close()

    async def test_archive_daily_command_updates_backup_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace_path = Path(tmp_dir) / "default"
            workspace_path.mkdir(parents=True, exist_ok=True)
            store = StateStore(Path(tmp_dir) / "state.db")
            store.create_workspace(
                WorkspaceRecord(
                    agent_id="agent-a",
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=workspace_path,
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            runner = _FakeRunner(
                CliRunResult(session_id="sess-1", answer="done", raw_output="")
            )
            registry = _FakeRegistry(runner)
            feishu = _FakeFeishuClient()
            executor = TaskExecutor(
                settings=self._build_settings(tmp_dir),
                agent=self._build_agent(),
                store=store,
                cli_registry=registry,
                feishu_client=feishu,
            )
            archive_service = WorkspaceArchiveService(
                store=store,
                archive_root=Path(tmp_dir) / "archive",
                interval_seconds=12 * 60 * 60,
            )
            chat = ChatService(
                settings=self._build_settings(tmp_dir),
                agent=self._build_agent(),
                store=store,
                workspace_manager=_FakeWorkspaceManager(),
                cli_registry=registry,
                feishu_client=feishu,
                task_executor=executor,
                archive_service=archive_service,
            )

            await chat.handle_message(
                FeishuInboundMessage(
                    agent_id="agent-a",
                    bot_app_id="bot-app",
                    owner_id="ou_1",
                    conversation_id="conv_1",
                    message_id="msg-1",
                    message_type="text",
                    content="/archive daily 03:15",
                    reply_target=FeishuReplyTarget("ou_1", "open_id"),
                )
            )

            self.assertIn("Archive schedule updated.", feishu.messages[-1][2])
            self.assertEqual(store.get_app_setting("archive.daily_time"), "03:15")
            self.assertEqual(archive_service.daily_time, "03:15")
            store.close()
