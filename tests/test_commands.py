import unittest

from light_claw.commands import help_text, parse_command


class CommandsTest(unittest.TestCase):
    def test_parse_workspace_create(self) -> None:
        command = parse_command("/workspace create Research Bot")
        self.assertIsNotNone(command)
        self.assertEqual(command.kind, "workspace_create")
        self.assertEqual(command.argument, "Research Bot")

    def test_parse_workspace_use(self) -> None:
        command = parse_command("/workspace use 2")
        self.assertIsNotNone(command)
        self.assertEqual(command.kind, "workspace_use")
        self.assertEqual(command.argument, "2")

    def test_parse_cli_use(self) -> None:
        command = parse_command("/cli use codex")
        self.assertIsNotNone(command)
        self.assertEqual(command.kind, "cli_use")
        self.assertEqual(command.argument, "codex")

    def test_help_text_includes_workspace_commands(self) -> None:
        text = help_text()
        self.assertIn("/workspace list", text)
        self.assertIn("/cli list", text)
        self.assertIn("/reset", text)


if __name__ == "__main__":
    unittest.main()
