from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Callable

from ..models import CliRunResult


PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


class CodexCliRuntimeError(RuntimeError):
    """Raised when the local Codex CLI runtime cannot complete a run."""


def parse_codex_cli_output(raw_output: str) -> CliRunResult:
    """Extract the resumed thread id and final agent text from Codex JSONL output.

    Args:
        raw_output: Raw stdout returned by `codex exec --json`.
    """
    session_id = None
    answer = ""
    for line in raw_output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and isinstance(
            event.get("thread_id"), str
        ):
            session_id = event["thread_id"]
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            answer = item["text"]
    if not answer:
        answer = "(Codex returned no agent_message output.)"
    return CliRunResult(session_id=session_id, answer=answer, raw_output=raw_output)


class CodexCliRuntime:
    """Run prompts through the local `codex` CLI and resume prior threads when needed."""

    provider_id = "codex"
    display_name = "Codex"

    def __init__(
        self,
        codex_bin: str = "codex",
        sandbox: str = "full-auto",
        default_model: str | None = None,
        default_search: bool = False,
        timeout_min_seconds: int = 180,
        timeout_max_seconds: int = 900,
        timeout_per_char_ms: int = 80,
        stall_timeout_seconds: int = 120,
    ) -> None:
        self.codex_bin = codex_bin
        self.sandbox = sandbox
        self.default_model = default_model
        self.default_search = default_search
        self.timeout_min_seconds = timeout_min_seconds
        self.timeout_max_seconds = timeout_max_seconds
        self.timeout_per_char_ms = timeout_per_char_ms
        self.stall_timeout_seconds = stall_timeout_seconds

    async def run(
        self,
        prompt: str,
        workspace_dir: Path,
        session_id: str | None = None,
        model: str | None = None,
        search: bool | None = None,
        on_activity: Callable[[], None] | None = None,
    ) -> CliRunResult:
        """Execute a prompt in the workspace through the Codex CLI.

        Args:
            prompt: Full prompt text to send to Codex.
            workspace_dir: Workspace directory that Codex should treat as the current repo.
            session_id: Existing Codex thread id to resume, if any.
            model: Optional model override for this run.
            search: Optional search override for this run.
            on_activity: Callback triggered whenever stdout or stderr produces activity.
        """
        command_args = self._build_command_args(
            prompt=prompt,
            workspace_dir=workspace_dir,
            session_id=session_id,
            model=model or self.default_model,
            search=self.default_search if search is None else search,
        )
        timeout_seconds = self._resolve_timeout_seconds(prompt)
        try:
            process = await asyncio.create_subprocess_exec(
                self.codex_bin,
                *command_args,
                cwd=str(workspace_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise CodexCliRuntimeError(
                "failed to start codex: {}".format(str(exc))
            ) from exc

        last_activity_at = asyncio.get_running_loop().time()

        def record_activity() -> None:
            nonlocal last_activity_at
            last_activity_at = asyncio.get_running_loop().time()
            if on_activity is not None:
                on_activity()

        stdout_task = asyncio.create_task(
            self._read_stream(process.stdout, record_activity)
        )
        stderr_task = asyncio.create_task(
            self._read_stream(process.stderr, record_activity)
        )

        started_at = asyncio.get_running_loop().time()
        try:
            while True:
                if process.returncode is not None:
                    break
                if asyncio.get_running_loop().time() - started_at > timeout_seconds:
                    raise asyncio.TimeoutError()
                if (
                    self.stall_timeout_seconds > 0
                    and asyncio.get_running_loop().time() - last_activity_at
                    > self.stall_timeout_seconds
                ):
                    raise CodexCliRuntimeError(
                        "codex stalled for {} seconds".format(
                            self.stall_timeout_seconds
                        )
                    )
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise CodexCliRuntimeError(
                "codex timed out after {} seconds".format(timeout_seconds)
            ) from exc
        except CodexCliRuntimeError:
            process.kill()
            await process.wait()
            raise

        stdout_text = await stdout_task
        stderr_text = await stderr_task
        if process.returncode != 0:
            detail = stderr_text.strip() or stdout_text.strip() or "unknown error"
            raise CodexCliRuntimeError(
                "codex exited with code {}: {}".format(process.returncode, detail)
            )

        parsed = parse_codex_cli_output(stdout_text)
        return CliRunResult(
            session_id=parsed.session_id or session_id,
            answer=parsed.answer,
            raw_output=stdout_text,
        )

    async def _read_stream(
        self,
        stream: asyncio.StreamReader | None,
        on_activity: Callable[[], None] | None = None,
    ) -> str:
        """Read one subprocess stream until EOF and return the decoded text.

        Args:
            stream: Subprocess stdout or stderr stream.
            on_activity: Callback triggered whenever a new chunk is read.
        """
        if stream is None:
            return ""
        chunks = []
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            if on_activity is not None:
                on_activity()
            chunks.append(chunk.decode("utf-8", errors="replace"))
        return "".join(chunks)

    def _build_command_args(
        self,
        prompt: str,
        workspace_dir: Path,
        session_id: str | None,
        model: str | None,
        search: bool,
    ) -> list[str]:
        """Build the exact `codex` command arguments for one run.

        Args:
            prompt: Prompt text to append as the final CLI argument.
            workspace_dir: Workspace directory passed to `codex --cd`.
            session_id: Existing Codex thread id to resume, if any.
            model: Model override for this run.
            search: Whether to enable Codex search for this run.
        """
        sandbox_flag = (
            "--dangerously-bypass-approvals-and-sandbox"
            if self.sandbox == "none"
            else "--full-auto"
        )
        if session_id:
            command_args = [
                "exec",
                "resume",
                session_id,
                "--json",
                sandbox_flag,
                "--skip-git-repo-check",
            ]
        else:
            command_args = [
                "exec",
                "--json",
                sandbox_flag,
                "--skip-git-repo-check",
            ]

        if model:
            command_args.extend(["--model", model])
        command_args.extend(self._build_sandbox_config_args())
        command_args.extend(self._build_proxy_config_args())
        command_args = ["--cd", str(workspace_dir)] + command_args
        if search:
            command_args = ["--search"] + command_args
        command_args.append(prompt)
        return command_args

    def _build_sandbox_config_args(self) -> list[str]:
        """Build additional CLI args needed for the configured sandbox mode."""
        if self.sandbox == "none":
            return []
        return ["--config", "sandbox_workspace_write.network_access=true"]

    def _build_proxy_config_args(self) -> list[str]:
        """Forward host proxy settings into Codex sandbox shell commands."""
        command_args: list[str] = []
        for key, value in self._read_proxy_environment().items():
            command_args.extend(
                [
                    "--config",
                    f"shell_environment_policy.set.{key}={json.dumps(value)}",
                ]
            )
        return command_args

    def _read_proxy_environment(self) -> dict[str, str]:
        """Read the proxy-related environment variables that should be forwarded."""
        proxy_env: dict[str, str] = {}
        for key in PROXY_ENV_KEYS:
            value = os.getenv(key)
            if value:
                proxy_env[key] = value
        return proxy_env

    def _resolve_timeout_seconds(self, prompt: str) -> int:
        """Scale the total timeout with prompt size while honoring min/max bounds.

        Args:
            prompt: Prompt text whose length is used for the timeout estimate.
        """
        estimate = self.timeout_min_seconds + int(
            max(0, len(prompt)) * max(0, self.timeout_per_char_ms) / 1000
        )
        return max(
            self.timeout_min_seconds,
            min(self.timeout_max_seconds, estimate),
        )
