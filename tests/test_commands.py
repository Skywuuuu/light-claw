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

    def test_parse_task_and_cron_commands(self) -> None:
        task_command = parse_command("/task create Review open loops")
        self.assertIsNotNone(task_command)
        self.assertEqual(task_command.kind, "task_create")
        self.assertEqual(task_command.argument, "Review open loops")

        cron_command = parse_command("/cron every 60 1")
        self.assertIsNotNone(cron_command)
        self.assertEqual(cron_command.kind, "cron_every")
        self.assertEqual(cron_command.argument, "60 1")

    def test_help_text_includes_workspace_commands(self) -> None:
        text = help_text()
        self.assertIn("/workspace list", text)
        self.assertIn("/cli list", text)
        self.assertIn("/task list", text)
        self.assertIn("/cron every <seconds> <task_id>", text)
        self.assertIn("/reset", text)


if __name__ == "__main__":
    unittest.main()
