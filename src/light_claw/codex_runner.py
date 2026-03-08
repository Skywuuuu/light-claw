from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from .models import CliRunResult


class CodexRunnerError(RuntimeError):
    pass


def parse_codex_jsonl(raw_output: str) -> CliRunResult:
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


class CodexRunner:
    def __init__(
        self,
        codex_bin: str = "codex",
        sandbox: str = "full-auto",
        default_model: Optional[str] = None,
        default_search: bool = False,
        timeout_min_seconds: int = 180,
        timeout_max_seconds: int = 900,
        timeout_per_char_ms: int = 80,
    ) -> None:
        self.codex_bin = codex_bin
        self.sandbox = sandbox
        self.default_model = default_model
        self.default_search = default_search
        self.timeout_min_seconds = timeout_min_seconds
        self.timeout_max_seconds = timeout_max_seconds
        self.timeout_per_char_ms = timeout_per_char_ms

    async def run(
        self,
        prompt: str,
        workspace_dir: Path,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        search: Optional[bool] = None,
    ) -> CliRunResult:
        args = self._build_args(
            prompt=prompt,
            workspace_dir=workspace_dir,
            session_id=session_id,
            model=model or self.default_model,
            search=self.default_search if search is None else search,
        )
        timeout = self._resolve_timeout_seconds(prompt)
        process = await asyncio.create_subprocess_exec(
            self.codex_bin,
            *args,
            cwd=str(workspace_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_task = asyncio.create_task(self._read_stream(process.stdout))
        stderr_task = asyncio.create_task(self._read_stream(process.stderr))

        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.wait()
            raise CodexRunnerError(
                "codex timed out after {} seconds".format(timeout)
            ) from exc

        stdout_text = await stdout_task
        stderr_text = await stderr_task
        if process.returncode != 0:
            detail = stderr_text.strip() or stdout_text.strip() or "unknown error"
            raise CodexRunnerError(
                "codex exited with code {}: {}".format(process.returncode, detail)
            )

        parsed = parse_codex_jsonl(stdout_text)
        return CliRunResult(
            session_id=parsed.session_id or session_id,
            answer=parsed.answer,
            raw_output=stdout_text,
        )

    async def _read_stream(
        self, stream: Optional[asyncio.StreamReader]
    ) -> str:
        if stream is None:
            return ""
        chunks = []
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", errors="replace"))
        return "".join(chunks)

    def _build_args(
        self,
        prompt: str,
        workspace_dir: Path,
        session_id: Optional[str],
        model: Optional[str],
        search: bool,
    ) -> list[str]:
        sandbox_flag = (
            "--dangerously-bypass-approvals-and-sandbox"
            if self.sandbox == "none"
            else "--full-auto"
        )
        if session_id:
            args = [
                "exec",
                "resume",
                session_id,
                "--json",
                sandbox_flag,
                "--skip-git-repo-check",
            ]
        else:
            args = ["exec", "--json", sandbox_flag, "--skip-git-repo-check"]

        if model:
            args.extend(["--model", model])
        args = ["--cd", str(workspace_dir)] + args
        if search:
            args = ["--search"] + args
        args.append(prompt)
        return args

    def _resolve_timeout_seconds(self, prompt: str) -> int:
        estimate = self.timeout_min_seconds + int(
            max(0, len(prompt)) * max(0, self.timeout_per_char_ms) / 1000
        )
        return max(
            self.timeout_min_seconds,
            min(self.timeout_max_seconds, estimate),
        )
