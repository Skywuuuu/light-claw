import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from light_claw.config import Settings
from light_claw.server import create_app


class ServerTest(unittest.TestCase):
    def test_health_endpoints_are_ready_for_local_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_ARCHIVE_ENABLED": "false",
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir) / "repo")

            with TestClient(create_app(settings)) as client:
                self.assertEqual(client.get("/livez").status_code, 200)
                self.assertEqual(client.get("/healthz").json(), {"ok": True})
                ready_response = client.get("/readyz")
                self.assertEqual(ready_response.status_code, 200)
                self.assertTrue(ready_response.json()["ready"])
                details = client.get("/healthz/details").json()
                self.assertIn("heartbeat", details)
                self.assertIn("cron", details)

    def test_url_verification_uses_matching_agent_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            agents_file = Path(tmp_dir) / "agents.json"
            agents_file.write_text(
                """
                {
                  "agents": [
                    {
                      "agent_id": "writer",
                      "app_id": "cli_writer",
                      "app_secret": "writer_secret",
                      "verification_token": "writer_token"
                    },
                    {
                      "agent_id": "reviewer",
                      "app_id": "cli_reviewer",
                      "app_secret": "reviewer_secret",
                      "verification_token": "reviewer_token"
                    }
                  ]
                }
                """.strip(),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "true",
                    "FEISHU_EVENT_MODE": "webhook",
                    "LIGHT_CLAW_AGENTS_FILE": str(agents_file),
                    "LIGHT_CLAW_ARCHIVE_ENABLED": "false",
                },
                clear=False,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir) / "repo")

            with TestClient(create_app(settings)) as client:
                response = client.post(
                    "/feishu/events",
                    json={
                        "type": "url_verification",
                        "token": "reviewer_token",
                        "challenge": "ok",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {"challenge": "ok"})


if __name__ == "__main__":
    unittest.main()
