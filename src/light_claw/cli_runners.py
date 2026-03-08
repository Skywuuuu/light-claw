from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Protocol

from .codex_runner import CodexRunner, CodexRunnerError
from .config import Settings
from .models import CliProviderInfo, CliRunResult


class CliRunnerError(RuntimeError):
    pass


class CliRunner(Protocol):
    provider_id: str
    display_name: str

    async def run(
        self,
        prompt: str,
        workspace_dir,
        session_id: str | None = None,
    ) -> CliRunResult:
        ...


@dataclass
class CodexCliAdapter:
    runner: CodexRunner
    provider_id: str = "codex"
    display_name: str = "Codex"

    async def run(
        self,
        prompt: str,
        workspace_dir,
        session_id: str | None = None,
    ) -> CliRunResult:
        try:
            return await self.runner.run(
                prompt=prompt,
                workspace_dir=workspace_dir,
                session_id=session_id,
            )
        except CodexRunnerError as exc:
            raise CliRunnerError(str(exc)) from exc


class CliRunnerRegistry:
    def __init__(
        self,
        providers: Iterable[CliProviderInfo],
        runners: Dict[str, CliRunner],
    ) -> None:
        self._providers = {provider.provider_id: provider for provider in providers}
        self._runners = dict(runners)

    @classmethod
    def from_settings(cls, settings: Settings) -> "CliRunnerRegistry":
        codex_runner = CodexRunner(
            codex_bin=settings.codex_bin,
            sandbox=settings.codex_sandbox,
            default_model=settings.codex_model,
            default_search=settings.codex_search,
            timeout_min_seconds=settings.codex_timeout_min_seconds,
            timeout_max_seconds=settings.codex_timeout_max_seconds,
            timeout_per_char_ms=settings.codex_timeout_per_char_ms,
        )
        codex_adapter = CodexCliAdapter(codex_runner)
        providers = [
            CliProviderInfo(
                provider_id="codex",
                display_name="Codex",
                description="OpenAI Codex CLI adapter.",
                available=True,
            ),
            CliProviderInfo(
                provider_id="claude-code",
                display_name="Claude Code",
                description="Reserved provider slot for a future Claude Code adapter.",
                available=False,
            ),
            CliProviderInfo(
                provider_id="custom",
                display_name="Custom CLI",
                description="Reserved provider slot for custom CLI adapters.",
                available=False,
            ),
        ]
        return cls(providers=providers, runners={"codex": codex_adapter})

    def list_providers(self) -> List[CliProviderInfo]:
        return list(self._providers.values())

    def get_provider(self, provider_id: str) -> CliProviderInfo | None:
        return self._providers.get(provider_id.strip().lower())

    def default_provider_id(self, requested: str) -> str:
        provider_id = requested.strip().lower()
        if provider_id in self._runners:
            return provider_id
        return "codex"

    def validate_selectable(self, provider_id: str) -> tuple[bool, str]:
        normalized = provider_id.strip().lower()
        provider = self.get_provider(normalized)
        if provider is None:
            return False, "Unknown CLI provider. Use `/cli list`."
        if normalized not in self._runners:
            return (
                False,
                "{} is reserved but not wired yet. {}".format(
                    provider.display_name,
                    provider.description,
                ),
            )
        return True, ""

    def get_runner(self, provider_id: str) -> CliRunner:
        normalized = provider_id.strip().lower()
        runner = self._runners.get(normalized)
        if runner:
            return runner
        provider = self.get_provider(normalized)
        if provider:
            raise CliRunnerError(
                "{} is configured as a provider slot, but no adapter is implemented yet.".format(
                    provider.display_name
                )
            )
        raise CliRunnerError("Unknown CLI provider: {}".format(provider_id))
