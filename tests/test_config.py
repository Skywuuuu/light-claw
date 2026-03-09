import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from light_claw.config import Settings


class SettingsCompatibilityTest(unittest.TestCase):
    def test_archive_defaults_to_sibling_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "light-claw"
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_ARCHIVE_DIR": "",
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=base_dir)

        self.assertEqual(settings.archive_dir, (Path(tmp_dir) / "light-claw-data").resolve())
        self.assertTrue(settings.archive_enabled)
        self.assertEqual(settings.archive_interval_seconds, 12 * 60 * 60)
        self.assertTrue(settings.task_heartbeat_enabled)
        self.assertEqual(settings.task_heartbeat_interval_seconds, 30 * 60)
        self.assertTrue(settings.cron_enabled)
        self.assertEqual(settings.cron_poll_interval_seconds, 60)

    def test_prefers_light_claw_env_vars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_dir = Path(tmp_dir) / "light-data"
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_DATA_DIR": str(data_dir),
                    "LIGHT_CLAW_SANDBOX": "danger-full-access",
                    "CODEX_CLAW_SANDBOX": "full-auto",
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir) / "repo")

        self.assertEqual(settings.data_dir, data_dir.resolve())
        self.assertEqual(settings.database_path, data_dir.resolve() / "light-claw.db")
        self.assertEqual(settings.codex_sandbox, "none")

    def test_accepts_legacy_sandbox_env_vars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            expected_data_dir = Path(tmp_dir) / "repo" / ".data"
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_DATA_DIR": "",
                    "CODEX_SANDBOX": "workspace-write",
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir) / "repo")

        self.assertEqual(settings.data_dir, expected_data_dir.resolve())
        self.assertEqual(
            settings.database_path,
            expected_data_dir.resolve() / "light-claw.db",
        )
        self.assertEqual(settings.codex_sandbox, "full-auto")

    def test_blank_data_dir_falls_back_to_default_data_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "repo"
            expected_data_dir = base_dir / ".data"
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_DATA_DIR": "",
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=base_dir)

        self.assertEqual(settings.data_dir, expected_data_dir.resolve())
        self.assertEqual(
            settings.database_path,
            expected_data_dir.resolve() / "light-claw.db",
        )

    def test_relative_data_dir_is_resolved_from_base_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "repo"
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_DATA_DIR": ".isolated-data",
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=base_dir)

        self.assertEqual(settings.data_dir, (base_dir / ".isolated-data").resolve())
        self.assertEqual(
            settings.database_path,
            (base_dir / ".isolated-data" / "light-claw.db").resolve(),
        )

    def test_explicit_base_dir_does_not_load_cwd_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            cwd_dir = Path(tmp_dir) / "live-instance"
            base_dir = Path(tmp_dir) / "test-instance"
            cwd_dir.mkdir(parents=True)
            (cwd_dir / ".env").write_text(
                "LIGHT_CLAW_DATA_DIR=.live-data\n",
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            try:
                os.chdir(cwd_dir)
                with patch.dict(
                    os.environ,
                    {"FEISHU_ENABLED": "false", "LIGHT_CLAW_DATA_DIR": ""},
                    clear=False,
                ):
                    settings = Settings.from_env(base_dir=base_dir)
            finally:
                os.chdir(old_cwd)

        self.assertEqual(settings.data_dir, (base_dir / ".data").resolve())

    def test_reuses_legacy_database_filename_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "repo"
            data_dir = base_dir / ".data"
            data_dir.mkdir(parents=True)
            legacy_database_path = data_dir / "codex-claw.db"
            legacy_database_path.write_text("", encoding="utf-8")

            with patch.dict(
                os.environ,
                {"FEISHU_ENABLED": "false", "LIGHT_CLAW_DATA_DIR": ""},
                clear=False,
            ):
                settings = Settings.from_env(base_dir=base_dir)

        self.assertEqual(settings.database_path, legacy_database_path.resolve())

    def test_long_connection_mode_does_not_require_verification_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "LIGHT_CLAW_DATA_DIR": str(Path(tmp_dir) / "data"),
                    "FEISHU_ENABLED": "true",
                    "FEISHU_EVENT_MODE": "long_connection",
                    "FEISHU_APP_ID": "cli_test",
                    "FEISHU_APP_SECRET": "secret",
                    "FEISHU_VERIFICATION_TOKEN": "",
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir) / "repo")

        self.assertEqual(settings.feishu_event_mode, "long_connection")
        self.assertIsNone(settings.feishu_verification_token)
        self.assertEqual(settings.primary_agent.agent_id, "default")

    def test_webhook_mode_requires_verification_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "LIGHT_CLAW_DATA_DIR": str(Path(tmp_dir) / "data"),
                    "FEISHU_ENABLED": "true",
                    "FEISHU_EVENT_MODE": "webhook",
                    "FEISHU_APP_ID": "cli_test",
                    "FEISHU_APP_SECRET": "secret",
                    "FEISHU_VERIFICATION_TOKEN": "",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(ValueError, "FEISHU_VERIFICATION_TOKEN"):
                    Settings.from_env(base_dir=Path(tmp_dir) / "repo")

    def test_archive_interval_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_ARCHIVE_INTERVAL_SECONDS": "0",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    ValueError, "LIGHT_CLAW_ARCHIVE_INTERVAL_SECONDS"
                ):
                    Settings.from_env(base_dir=Path(tmp_dir) / "repo")

    def test_task_runtime_intervals_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_TASK_HEARTBEAT_INTERVAL_SECONDS": "0",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    ValueError, "LIGHT_CLAW_TASK_HEARTBEAT_INTERVAL_SECONDS"
                ):
                    Settings.from_env(base_dir=Path(tmp_dir) / "repo")

            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_CRON_POLL_INTERVAL_SECONDS": "0",
                },
                clear=False,
            ):
                with self.assertRaisesRegex(
                    ValueError, "LIGHT_CLAW_CRON_POLL_INTERVAL_SECONDS"
                ):
                    Settings.from_env(base_dir=Path(tmp_dir) / "repo")

    def test_supports_multi_agent_json_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            agents_file = Path(tmp_dir) / "agents.json"
            agents_file.write_text(
                """
                {
                  "agents": [
                    {
                      "agent_id": "writer",
                      "name": "Writer",
                      "app_id": "cli_writer",
                      "app_secret": "writer_secret",
                      "verification_token": "writer_token",
                      "skills_path": "skills/writer.md"
                    },
                    {
                      "agent_id": "reviewer",
                      "name": "Reviewer",
                      "app_id": "cli_reviewer",
                      "app_secret": "reviewer_secret",
                      "verification_token": "reviewer_token",
                      "mcp_config_path": "mcp/reviewer.json"
                    }
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "true",
                    "FEISHU_EVENT_MODE": "webhook",
                    "LIGHT_CLAW_AGENTS_FILE": str(agents_file),
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir) / "repo")

        self.assertEqual(len(settings.agents), 2)
        self.assertEqual(settings.get_agent("writer").feishu_app_id, "cli_writer")
        self.assertEqual(
            settings.get_agent("writer").skills_path,
            (Path(tmp_dir) / "repo" / "skills" / "writer.md").resolve(),
        )
        self.assertEqual(
            settings.get_agent("reviewer").mcp_config_path,
            (Path(tmp_dir) / "repo" / "mcp" / "reviewer.json").resolve(),
        )


if __name__ == "__main__":
    unittest.main()
