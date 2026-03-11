import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from light_claw.config import DEFAULT_AGENT_ID
from light_claw.config import Settings
from light_claw.models import WorkspaceRecord
from light_claw.server import create_app
from light_claw.store import StateStore


class ServerTest(unittest.TestCase):
    def test_health_endpoints_are_ready_for_local_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_ARCHIVE_ENABLED": "false",
                    "LIGHT_CLAW_TASK_HEARTBEAT_ENABLED": "false",
                    "LIGHT_CLAW_CRON_ENABLED": "false",
                    "LIGHT_CLAW_DATA_DIR": "",
                },
                clear=True,
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
                    "LIGHT_CLAW_DATA_DIR": "",
                },
                clear=True,
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

    def test_build_services_recovers_orphaned_task_runs_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_ARCHIVE_ENABLED": "false",
                    "LIGHT_CLAW_TASK_HEARTBEAT_ENABLED": "false",
                    "LIGHT_CLAW_CRON_ENABLED": "false",
                    "LIGHT_CLAW_DATA_DIR": "",
                },
                clear=True,
            ):
                settings = Settings.from_env(base_dir=repo_dir)

            seed_store = StateStore(settings.database_path)
            seed_store.create_workspace(
                WorkspaceRecord(
                    agent_id=DEFAULT_AGENT_ID,
                    owner_id="ou_1",
                    workspace_id="default",
                    name="Default",
                    path=settings.workspaces_dir / DEFAULT_AGENT_ID,
                    cli_provider="codex",
                    created_at=0.0,
                    updated_at=0.0,
                )
            )
            task = seed_store.create_workspace_task(
                DEFAULT_AGENT_ID,
                "ou_1",
                "default",
                "Recover on startup",
                next_run_at=60.0,
            )
            run = seed_store.claim_workspace_task(
                DEFAULT_AGENT_ID,
                "ou_1",
                "default",
                task.task_id,
                trigger_source="cron",
            )
            self.assertIsNotNone(run)
            seed_store.close()

            with TestClient(create_app(settings)) as client:
                latest_run = client.app.state.services.store.get_latest_task_run(
                    DEFAULT_AGENT_ID,
                    "ou_1",
                    "default",
                    task.task_id,
                )
                self.assertIsNotNone(latest_run)
                self.assertEqual(latest_run.status, "failed")
                self.assertEqual(
                    latest_run.error_message,
                    "Recovered orphaned task run from a previous process.",
                )
                updated_task = client.app.state.services.store.get_workspace_task(
                    DEFAULT_AGENT_ID,
                    "ou_1",
                    "default",
                    task.task_id,
                )
                self.assertIsNotNone(updated_task)
                self.assertEqual(updated_task.status, "failed")
                self.assertEqual(updated_task.next_run_at, 60.0)

    def test_memory_api_supports_search_get_and_append_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(
                os.environ,
                {
                    "FEISHU_ENABLED": "false",
                    "LIGHT_CLAW_ARCHIVE_ENABLED": "false",
                    "LIGHT_CLAW_TASK_HEARTBEAT_ENABLED": "false",
                    "LIGHT_CLAW_CRON_ENABLED": "false",
                    "LIGHT_CLAW_DATA_DIR": "",
                },
                clear=True,
            ):
                settings = Settings.from_env(base_dir=Path(tmp_dir) / "repo")

            with TestClient(create_app(settings)) as client:
                workspace_path = settings.workspaces_dir / DEFAULT_AGENT_ID
                workspace_path.mkdir(parents=True, exist_ok=True)
                (workspace_path / "AGENTS.md").write_text(
                    "# AGENTS.md\n\n- Preferred style: concise\n",
                    encoding="utf-8",
                )
                client.app.state.services.store.create_workspace(
                    WorkspaceRecord(
                        agent_id=DEFAULT_AGENT_ID,
                        owner_id="ou_1",
                        workspace_id="default",
                        name="Default",
                        path=workspace_path,
                        cli_provider="codex",
                        created_at=0.0,
                        updated_at=0.0,
                    )
                )

                append_response = client.post(
                    f"/api/memory/{DEFAULT_AGENT_ID}/append",
                    json={"content": "Remember today", "entry_date": "2026-03-11"},
                )
                self.assertEqual(append_response.status_code, 200)
                self.assertEqual(
                    append_response.json(),
                    {"path": "memory/daily/2026-03-11.md"},
                )

                search_response = client.get(
                    f"/api/memory/{DEFAULT_AGENT_ID}/search",
                    params={"query": "Remember", "limit": 10},
                )
                self.assertEqual(search_response.status_code, 200)
                hits = search_response.json()["hits"]
                self.assertTrue(any(hit["scope"] == "memory" for hit in hits))

                get_response = client.get(
                    f"/api/memory/{DEFAULT_AGENT_ID}/get",
                    params={"path": "AGENTS.md"},
                )
                self.assertEqual(get_response.status_code, 200)
                self.assertEqual(get_response.json()["path"], "AGENTS.md")
                self.assertIn("Preferred style: concise", get_response.json()["content"])


if __name__ == "__main__":
    unittest.main()
