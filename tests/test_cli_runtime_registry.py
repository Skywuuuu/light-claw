import os
import unittest
from unittest.mock import patch

from light_claw.runtime import CliRuntimeRegistry
from light_claw.config import Settings


class CliRuntimeRegistryTest(unittest.TestCase):
    def test_registry_exposes_reserved_provider_slots(self) -> None:
        with patch.dict(os.environ, {"FEISHU_ENABLED": "false"}, clear=False):
            settings = Settings.from_env()
        registry = CliRuntimeRegistry.from_settings(settings)

        provider_ids = [provider.provider_id for provider in registry.list_providers()]
        self.assertIn("codex", provider_ids)
        self.assertIn("claude-code", provider_ids)
        self.assertIn("custom", provider_ids)

    def test_reserved_provider_is_not_selectable(self) -> None:
        with patch.dict(os.environ, {"FEISHU_ENABLED": "false"}, clear=False):
            settings = Settings.from_env()
        registry = CliRuntimeRegistry.from_settings(settings)

        ok, reason = registry.validate_selectable("claude-code")
        self.assertFalse(ok)
        self.assertIn("reserved", reason.lower())


if __name__ == "__main__":
    unittest.main()
