import unittest

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


if __name__ == "__main__":
    unittest.main()
