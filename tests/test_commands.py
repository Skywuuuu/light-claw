import unittest

from light_claw.commands import help_text, parse_command


class CommandsTest(unittest.TestCase):
    def test_parse_cli_use(self) -> None:
        command = parse_command("/cli use codex")
        self.assertIsNotNone(command)
        self.assertEqual(command.kind, "cli_use")
        self.assertEqual(command.argument, "codex")

    def test_removed_commands_return_none(self) -> None:
        self.assertIsNone(parse_command("/task create Review open loops"))
        self.assertIsNone(parse_command("/cron every 60 1"))
        self.assertIsNone(parse_command("/archive daily 03:15"))

    def test_help_text_lists_supported_commands(self) -> None:
        text = help_text()
        self.assertNotIn("/workspace", text)
        self.assertNotIn("/archive", text)
        self.assertNotIn("/task", text)
        self.assertNotIn("/cron", text)
        self.assertIn("/cli list", text)
        self.assertIn("/cli current", text)
        self.assertIn("/cli use <provider>", text)
        self.assertIn("/reset", text)

    def test_parse_skills(self) -> None:
        command = parse_command("/skills")
        self.assertIsNotNone(command)
        self.assertEqual(command.kind, "skills")
        self.assertIsNone(command.argument)

    def test_help_text_includes_skills(self) -> None:
        text = help_text()
        self.assertIn("/skills", text)


if __name__ == "__main__":
    unittest.main()
