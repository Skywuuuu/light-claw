import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from light_claw.config import Settings


class SettingsCompatibilityTest(unittest.TestCase):
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

    def test_reuses_legacy_database_filename_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "repo"
            data_dir = base_dir / ".data"
            data_dir.mkdir(parents=True)
            legacy_database_path = data_dir / "codex-claw.db"
            legacy_database_path.write_text("", encoding="utf-8")

            with patch.dict(os.environ, {"FEISHU_ENABLED": "false"}, clear=False):
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


if __name__ == "__main__":
    unittest.main()
