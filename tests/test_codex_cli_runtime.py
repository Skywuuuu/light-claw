import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from light_claw.runtime import CodexCliRuntime


class CodexCliRuntimeTest(unittest.TestCase):
    def test_build_args_forwards_proxy_env_to_sandbox_commands(self) -> None:
        runtime = CodexCliRuntime()
        workspace_dir = Path("/tmp/light-claw")
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://127.0.0.1:7890",
                "HTTPS_PROXY": "http://127.0.0.1:7890",
            },
            clear=False,
        ):
            args = runtime._build_command_args(
                prompt="test prompt",
                workspace_dir=workspace_dir,
                session_id=None,
                model=None,
                search=False,
            )

        self.assertIn(
            'shell_environment_policy.set.HTTP_PROXY="http://127.0.0.1:7890"',
            args,
        )
        self.assertIn(
            'shell_environment_policy.set.HTTPS_PROXY="http://127.0.0.1:7890"',
            args,
        )
        self.assertIn("sandbox_workspace_write.network_access=true", args)

    def test_build_args_skips_proxy_overrides_when_env_is_missing(self) -> None:
        runtime = CodexCliRuntime()
        workspace_dir = Path("/tmp/light-claw")
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "",
                "HTTPS_PROXY": "",
                "ALL_PROXY": "",
                "NO_PROXY": "",
                "http_proxy": "",
                "https_proxy": "",
                "all_proxy": "",
                "no_proxy": "",
            },
            clear=False,
        ):
            args = runtime._build_command_args(
                prompt="test prompt",
                workspace_dir=workspace_dir,
                session_id=None,
                model=None,
                search=False,
            )

        proxy_args = [
            value
            for value in args
            if value.startswith("shell_environment_policy.set.")
        ]
        self.assertEqual(proxy_args, [])
        self.assertIn("sandbox_workspace_write.network_access=true", args)

    def test_build_args_skips_workspace_network_override_without_sandbox(self) -> None:
        runtime = CodexCliRuntime(sandbox="none")
        workspace_dir = Path("/tmp/light-claw")
        args = runtime._build_command_args(
            prompt="test prompt",
            workspace_dir=workspace_dir,
            session_id=None,
            model=None,
            search=False,
        )

        self.assertNotIn("sandbox_workspace_write.network_access=true", args)


if __name__ == "__main__":
    unittest.main()


class CodexSkillDiscoveryTest(unittest.TestCase):
    def test_discovers_standalone_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            skill_dir = config_dir / "skills" / "slides"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: slides\ndescription: Create slide decks\n---\n",
                encoding="utf-8",
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("Standalone skills", result)
            self.assertEqual(
                result["Standalone skills"], [("slides", "Create slide decks")]
            )

    def test_skips_system_skills_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            system_dir = config_dir / "skills" / ".system"
            system_dir.mkdir(parents=True)
            (system_dir / "SKILL.md").write_text(
                "---\nname: imagegen\ndescription: Internal\n---\n",
                encoding="utf-8",
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            self.assertEqual(runtime.list_skills(), {})

    def test_discovers_plugin_skills_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = (
                config_dir / "plugins" / "cache" / "openai-curated"
                / "github" / "abc123"
            )
            skill_dir = plugin_dir / "skills" / "gh-fix-ci"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: gh-fix-ci\ndescription: Fix CI failures\n---\n",
                encoding="utf-8",
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "github", "version": "0.1.0"}),
                encoding="utf-8",
            )
            (config_dir / "config.toml").write_text(
                '[plugins."github@openai-curated"]\nenabled = true\n',
                encoding="utf-8",
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("github (v0.1.0)", result)
            self.assertEqual(
                result["github (v0.1.0)"], [("gh-fix-ci", "Fix CI failures")]
            )

    def test_combines_standalone_and_plugin_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            # Standalone skill
            standalone_dir = config_dir / "skills" / "slides"
            standalone_dir.mkdir(parents=True)
            (standalone_dir / "SKILL.md").write_text(
                "---\nname: slides\ndescription: Decks\n---\n", encoding="utf-8"
            )
            # Plugin skill
            plugin_dir = (
                config_dir / "plugins" / "cache" / "mp" / "gh" / "abc"
            )
            skill_dir = plugin_dir / "skills" / "fix-ci"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: fix-ci\ndescription: CI\n---\n", encoding="utf-8"
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "gh", "version": "1.0"}), encoding="utf-8"
            )
            (config_dir / "config.toml").write_text(
                '[plugins."gh@mp"]\nenabled = true\n', encoding="utf-8"
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("Standalone skills", result)
            self.assertIn("gh (v1.0)", result)

    def test_skips_disabled_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = (
                config_dir / "plugins" / "cache" / "mp" / "off" / "abc"
            )
            skill_dir = plugin_dir / "skills" / "nope"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: nope\ndescription: Disabled\n---\n", encoding="utf-8"
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "off", "version": "1.0"}), encoding="utf-8"
            )
            (config_dir / "config.toml").write_text(
                '[plugins."off@mp"]\nenabled = false\n', encoding="utf-8"
            )
            runtime = CodexCliRuntime(config_dir=config_dir)
            self.assertEqual(runtime.list_skills(), {})

    def test_shows_all_plugins_when_no_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = (
                config_dir / "plugins" / "cache" / "mp" / "test" / "abc"
            )
            skill_dir = plugin_dir / "skills" / "myskill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: myskill\ndescription: A skill\n---\n", encoding="utf-8"
            )
            meta_dir = plugin_dir / ".codex-plugin"
            meta_dir.mkdir()
            (meta_dir / "plugin.json").write_text(
                json.dumps({"name": "test", "version": "0.1"}), encoding="utf-8"
            )
            # No config.toml — should still show plugins
            runtime = CodexCliRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("test (v0.1)", result)

    def test_returns_empty_for_nonexistent_config_dir(self) -> None:
        runtime = CodexCliRuntime(config_dir=Path("/nonexistent"))
        self.assertEqual(runtime.list_skills(), {})
