from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Callable

from ..models import CliRunResult
from ._errors import CliRuntimeError


class ClaudeCodeRuntimeError(CliRuntimeError):
    """Raised when the local Claude Code runtime cannot complete a run."""


def parse_claude_code_output(raw_output: str) -> CliRunResult:
    """Extract the Claude Code session id and final answer from JSON output."""

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
        if not isinstance(event, dict):
            continue
        if isinstance(event.get("session_id"), str):
            session_id = event["session_id"]
        if isinstance(event.get("result"), str):
            answer = event["result"]
    if not answer:
        answer = "(Claude Code returned no result output.)"
    return CliRunResult(session_id=session_id, answer=answer, raw_output=raw_output)


class ClaudeCodeRuntime:
    """Run prompts through the local `claude` CLI and resume prior sessions when needed."""

    provider_id = "claude-code"
    display_name = "Claude Code"

    def __init__(
        self,
        claude_bin: str = "claude",
        default_model: str | None = None,
        permission_mode: str = "bypassPermissions",
        timeout_min_seconds: int = 180,
        timeout_max_seconds: int = 900,
        timeout_per_char_ms: int = 80,
        extra_writable_dirs: list[str] | None = None,
    ) -> None:
        self.claude_bin = claude_bin
        self.default_model = default_model
        self.permission_mode = permission_mode
        self.timeout_min_seconds = timeout_min_seconds
        self.timeout_max_seconds = timeout_max_seconds
        self.timeout_per_char_ms = timeout_per_char_ms
        self.extra_writable_dirs = extra_writable_dirs or []

    async def run(
        self,
        prompt: str,
        workspace_dir: Path,
        session_id: str | None = None,
        model: str | None = None,
        on_activity: Callable[[], None] | None = None,
    ) -> CliRunResult:
        command_args = self._build_command_args(
            prompt=prompt,
            session_id=session_id,
            model=model or self.default_model,
        )
        timeout_seconds = self._resolve_timeout_seconds(prompt)
        try:
            process = await asyncio.create_subprocess_exec(
                self.claude_bin,
                *command_args,
                cwd=str(workspace_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise ClaudeCodeRuntimeError(
                "failed to start claude code: {}".format(str(exc))
            ) from exc

        if on_activity is not None:
            on_activity()
        stdout_task = asyncio.create_task(self._read_stream(process.stdout, on_activity))
        stderr_task = asyncio.create_task(self._read_stream(process.stderr, on_activity))

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise ClaudeCodeRuntimeError(
                "claude code timed out after {} seconds".format(timeout_seconds)
            ) from exc

        stdout_text = await stdout_task
        stderr_text = await stderr_task
        if process.returncode != 0:
            detail = stderr_text.strip() or stdout_text.strip() or "unknown error"
            raise ClaudeCodeRuntimeError(
                "claude code exited with code {}: {}".format(process.returncode, detail)
            )

        parsed = parse_claude_code_output(stdout_text)
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
        *,
        prompt: str,
        session_id: str | None,
        model: str | None,
    ) -> list[str]:
        command_args = [
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            self.permission_mode,
        ]
        if session_id:
            command_args.extend(["--resume", session_id])
        if model:
            command_args.extend(["--model", model])
        for extra_dir in self.extra_writable_dirs:
            command_args.extend(["--add-dir", extra_dir])
        command_args.append(prompt)
        return command_args

    def _resolve_timeout_seconds(self, prompt: str) -> int:
        estimate = self.timeout_min_seconds + int(
            max(0, len(prompt)) * max(0, self.timeout_per_char_ms) / 1000
        )
        return max(
            self.timeout_min_seconds,
            min(self.timeout_max_seconds, estimate),
        )
