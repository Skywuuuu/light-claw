import json
import tempfile
import unittest
from pathlib import Path

from light_claw.runtime import ClaudeCodeRuntime, parse_claude_code_output


class ClaudeCodeRuntimeTest(unittest.TestCase):
    def test_parse_output_extracts_session_and_result(self) -> None:
        result = parse_claude_code_output(
            '{"type":"result","subtype":"success","session_id":"sess-123","result":"done"}\n'
        )

        self.assertEqual(result.session_id, "sess-123")
        self.assertEqual(result.answer, "done")

    def test_build_args_include_resume_and_extra_dirs(self) -> None:
        runtime = ClaudeCodeRuntime(
            default_model="claude-sonnet-4-5",
            extra_writable_dirs=["/tmp/shared", "/tmp/cache"],
        )
        args = runtime._build_command_args(
            prompt="test prompt",
            session_id="sess-123",
            model="claude-opus-4-1",
        )

        self.assertEqual(
            args[:6],
            [
                "-p",
                "--output-format",
                "json",
                "--permission-mode",
                "bypassPermissions",
                "--resume",
            ],
        )
        self.assertIn("sess-123", args)
        self.assertIn("--model", args)
        self.assertIn("claude-opus-4-1", args)
        self.assertEqual(args.count("--add-dir"), 2)
        self.assertEqual(args[-1], "test prompt")

    def test_build_args_without_resume_are_minimal(self) -> None:
        runtime = ClaudeCodeRuntime(permission_mode="acceptEdits")
        args = runtime._build_command_args(
            prompt="test prompt",
            session_id=None,
            model=None,
        )

        self.assertNotIn("--resume", args)
        self.assertNotIn("--model", args)
        self.assertEqual(
            args,
            [
                "-p",
                "--output-format",
                "json",
                "--permission-mode",
                "acceptEdits",
                "test prompt",
            ],
        )


class ClaudeCodeSkillDiscoveryTest(unittest.TestCase):
    def test_discovers_standalone_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            skill_dir = config_dir / "skills" / "pdf"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: pdf\ndescription: PDF tools\n---\n", encoding="utf-8"
            )
            runtime = ClaudeCodeRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("Standalone skills", result)
            self.assertEqual(result["Standalone skills"], [("pdf", "PDF tools")])

    def test_discovers_enabled_plugin_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = config_dir / "plugins" / "cache" / "mp" / "myplugin" / "1.0.0"
            skill_dir = plugin_dir / "skills" / "tdd"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: tdd\ndescription: Test-driven dev\n---\n", encoding="utf-8"
            )
            (config_dir / "plugins").mkdir(exist_ok=True)
            (config_dir / "plugins" / "installed_plugins.json").write_text(
                json.dumps({
                    "version": 2,
                    "plugins": {
                        "myplugin@mp": [
                            {"installPath": str(plugin_dir), "version": "1.0.0"}
                        ]
                    },
                }),
                encoding="utf-8",
            )
            (config_dir / "settings.json").write_text(
                json.dumps({"enabledPlugins": {"myplugin@mp": True}}),
                encoding="utf-8",
            )
            runtime = ClaudeCodeRuntime(config_dir=config_dir)
            result = runtime.list_skills()
            self.assertIn("myplugin (v1.0.0)", result)
            self.assertEqual(result["myplugin (v1.0.0)"], [("tdd", "Test-driven dev")])

    def test_skips_disabled_plugin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            plugin_dir = config_dir / "plugins" / "cache" / "mp" / "off" / "1.0"
            skill_dir = plugin_dir / "skills" / "nope"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\nname: nope\ndescription: Disabled\n---\n", encoding="utf-8"
            )
            (config_dir / "plugins").mkdir(exist_ok=True)
            (config_dir / "plugins" / "installed_plugins.json").write_text(
                json.dumps({
                    "version": 2,
                    "plugins": {
                        "off@mp": [{"installPath": str(plugin_dir), "version": "1.0"}]
                    },
                }),
                encoding="utf-8",
            )
            (config_dir / "settings.json").write_text(
                json.dumps({"enabledPlugins": {"off@mp": False}}),
                encoding="utf-8",
            )
            runtime = ClaudeCodeRuntime(config_dir=config_dir)
            self.assertEqual(runtime.list_skills(), {})

    def test_returns_empty_for_nonexistent_config_dir(self) -> None:
        runtime = ClaudeCodeRuntime(config_dir=Path("/nonexistent"))
        self.assertEqual(runtime.list_skills(), {})


if __name__ == "__main__":
    unittest.main()
