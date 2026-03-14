import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from light_claw.runtime import CliRuntimeRegistry
from light_claw.config import Settings


class CliRuntimeRegistryTest(unittest.TestCase):
    def test_registry_exposes_supported_provider_slots(self) -> None:
        with patch.dict(os.environ, {"FEISHU_ENABLED": "false"}, clear=False):
            settings = Settings.from_env()
        registry = CliRuntimeRegistry.from_settings(settings)

        provider_ids = [provider.provider_id for provider in registry.list_providers()]
        self.assertIn("codex", provider_ids)
        self.assertIn("claude-code", provider_ids)
        self.assertIn("custom", provider_ids)

    def test_claude_code_provider_is_selectable(self) -> None:
        with patch.dict(os.environ, {"FEISHU_ENABLED": "false"}, clear=False):
            settings = Settings.from_env()
        registry = CliRuntimeRegistry.from_settings(settings)

        ok, reason = registry.validate_selectable("claude-code")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_claude_runtime_uses_configured_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "CLAUDE_BIN": "/usr/local/bin/claude",
                    "CLAUDE_MODEL": "claude-sonnet-4-5",
                    "CLAUDE_PERMISSION_MODE": "acceptEdits",
                    "CLAUDE_ADD_DIRS": "/tmp/a:/tmp/b",
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir))
            registry = CliRuntimeRegistry.from_settings(settings)

        runtime = registry.get_runtime("claude-code")
        self.assertEqual(runtime.claude_bin, "/usr/local/bin/claude")
        self.assertEqual(runtime.default_model, "claude-sonnet-4-5")
        self.assertEqual(runtime.permission_mode, "acceptEdits")
        self.assertEqual(runtime.extra_writable_dirs, ["/tmp/a", "/tmp/b"])


if __name__ == "__main__":
    unittest.main()
