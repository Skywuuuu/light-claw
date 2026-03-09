import unittest

from light_claw.commands import help_text, parse_command


class CommandsTest(unittest.TestCase):
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

        task_status_command = parse_command("/task status 1")
        self.assertIsNotNone(task_status_command)
        self.assertEqual(task_status_command.kind, "task_status")
        self.assertEqual(task_status_command.argument, "1")

        cron_command = parse_command("/cron every 60 1")
        self.assertIsNotNone(cron_command)
        self.assertEqual(cron_command.kind, "cron_every")
        self.assertEqual(cron_command.argument, "60 1")

    def test_help_text_omits_workspace_commands(self) -> None:
        text = help_text()
        self.assertNotIn("/workspace", text)
        self.assertIn("/cli list", text)
        self.assertIn("/task list", text)
        self.assertIn("/task status <id|index>", text)
        self.assertIn("/cron every <seconds> <task_id>", text)
        self.assertIn("/reset", text)


if __name__ == "__main__":
    unittest.main()
