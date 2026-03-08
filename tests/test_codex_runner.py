import os
import unittest
from pathlib import Path
from unittest.mock import patch

from light_claw.codex_runner import CodexRunner


class CodexRunnerTest(unittest.TestCase):
    def test_build_args_forwards_proxy_env_to_sandbox_commands(self) -> None:
        runner = CodexRunner()
        workspace_dir = Path("/tmp/light-claw")
        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://127.0.0.1:7890",
                "HTTPS_PROXY": "http://127.0.0.1:7890",
            },
            clear=False,
        ):
            args = runner._build_args(
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

    def test_build_args_skips_proxy_overrides_when_env_is_missing(self) -> None:
        runner = CodexRunner()
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
            args = runner._build_args(
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


if __name__ == "__main__":
    unittest.main()
