from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, Iterable, List, Protocol

from ..config import AgentSettings, Settings
from ..models import CliProviderInfo, CliRunResult
from .claude_code import ClaudeCodeRuntime
from .codex_cli import CodexCliRuntime


from ._errors import CliRuntimeError  # noqa: F811 – re-export


class CliRuntime(Protocol):
    provider_id: str
    display_name: str

    async def run(
        self,
        prompt: str,
        workspace_dir: Path,
        session_id: str | None = None,
        on_activity: Callable[[], None] | None = None,
    ) -> CliRunResult:
        ...


class CliRuntimeRegistry:
    """Keep track of selectable CLI providers and the concrete runtimes behind them."""

    def __init__(
        self,
        providers: Iterable[CliProviderInfo],
        runtimes: Dict[str, CliRuntime],
    ) -> None:
        self._providers = {provider.provider_id: provider for provider in providers}
        self._runtimes = dict(runtimes)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        agent: AgentSettings | None = None,
    ) -> "CliRuntimeRegistry":
        """Build the runtime registry for one agent.

        Args:
            settings: Application settings shared across all agents.
            agent: Optional per-agent overrides for model, sandbox, and search.
        """
        codex_runtime = CodexCliRuntime(
            codex_bin=settings.codex_bin,
            sandbox=agent.codex_sandbox if agent else settings.codex_sandbox,
            default_model=agent.codex_model if agent else settings.codex_model,
            default_search=agent.codex_search if agent else settings.codex_search,
            timeout_min_seconds=settings.codex_timeout_min_seconds,
            timeout_max_seconds=settings.codex_timeout_max_seconds,
            timeout_per_char_ms=settings.codex_timeout_per_char_ms,
            stall_timeout_seconds=settings.codex_stall_timeout_seconds,
            extra_writable_dirs=settings.codex_add_dirs,
        )
        claude_runtime = ClaudeCodeRuntime(
            claude_bin=settings.claude_bin,
            default_model=settings.claude_model,
            permission_mode=settings.claude_permission_mode,
            timeout_min_seconds=settings.codex_timeout_min_seconds,
            timeout_max_seconds=settings.codex_timeout_max_seconds,
            timeout_per_char_ms=settings.codex_timeout_per_char_ms,
            extra_writable_dirs=settings.claude_add_dirs,
        )
        providers = [
            CliProviderInfo(
                provider_id="codex",
                display_name="Codex",
                description="OpenAI Codex CLI runtime.",
                available=True,
            ),
            CliProviderInfo(
                provider_id="claude-code",
                display_name="Claude Code",
                description="Anthropic Claude Code CLI runtime.",
                available=True,
            ),
            CliProviderInfo(
                provider_id="custom",
                display_name="Custom CLI",
                description="Reserved runtime slot for custom CLI integrations.",
                available=False,
            ),
        ]
        return cls(
            providers=providers,
            runtimes={
                "codex": codex_runtime,
                "claude-code": claude_runtime,
            },
        )

    def list_providers(self) -> List[CliProviderInfo]:
        return list(self._providers.values())

    def get_provider(self, provider_id: str) -> CliProviderInfo | None:
        return self._providers.get(provider_id.strip().lower())

    def default_provider_id(self, requested: str) -> str:
        provider_id = requested.strip().lower()
        if provider_id in self._runtimes:
            return provider_id
        return "codex"

    def validate_selectable(self, provider_id: str) -> tuple[bool, str]:
        """Validate whether the requested provider can run today.

        Args:
            provider_id: Provider id from workspace state or a `/cli use` command.
        """
        normalized = provider_id.strip().lower()
        provider = self.get_provider(normalized)
        if provider is None:
            return False, "Unknown CLI provider. Use `/cli list`."
        if normalized not in self._runtimes:
            return (
                False,
                "{} is reserved but not wired yet. {}".format(
                    provider.display_name,
                    provider.description,
                ),
            )
        return True, ""

    def get_runtime(self, provider_id: str) -> CliRuntime:
        """Return the concrete runtime for the selected provider id.

        Args:
            provider_id: Provider id stored on the workspace.
        """
        normalized = provider_id.strip().lower()
        runtime = self._runtimes.get(normalized)
        if runtime:
            return runtime
        provider = self.get_provider(normalized)
        if provider:
            raise CliRuntimeError(
                "{} is configured as a provider slot, but no runtime is implemented yet.".format(
                    provider.display_name
                )
            )
        raise CliRuntimeError("Unknown CLI provider: {}".format(provider_id))
