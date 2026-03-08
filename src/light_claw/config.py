from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:

    def load_dotenv() -> bool:
        return False


APP_NAME = "light-claw"
LEGACY_APP_NAME = "codex-claw"
DEFAULT_AGENT_ID = "default"


load_dotenv()


def _read_raw(name: str, *aliases: str) -> Optional[str]:
    for key in (name, *aliases):
        raw = os.getenv(key)
        if raw is not None:
            return raw
    return None


def _read_bool(name: str, default: bool, *aliases: str) -> bool:
    raw = _read_raw(name, *aliases)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_int(name: str, default: int, *aliases: str) -> int:
    raw = _read_raw(name, *aliases)
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def _read_str(name: str, default: str, *aliases: str) -> str:
    raw = _read_raw(name, *aliases)
    if raw is None:
        return default
    return raw.strip()


def _read_optional_str(name: str, *aliases: str) -> Optional[str]:
    raw = _read_raw(name, *aliases)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _read_feishu_event_mode() -> str:
    raw = _read_str("FEISHU_EVENT_MODE", "webhook").strip().lower()
    mapping = {
        "webhook": "webhook",
        "http": "webhook",
        "callback": "webhook",
        "long_connection": "long_connection",
        "long-connection": "long_connection",
        "longconnection": "long_connection",
        "ws": "long_connection",
        "websocket": "long_connection",
    }
    return mapping.get(raw, raw)


def _normalize_codex_sandbox(raw: str) -> str:
    mapping = {
        "full-auto": "full-auto",
        "workspace-write": "full-auto",
        "read-only": "full-auto",
        "seatbelt": "full-auto",
        "none": "none",
        "danger-full-access": "none",
    }
    return mapping.get(raw.strip().lower(), "full-auto")


def _read_codex_sandbox() -> str:
    raw = _read_str(
        "LIGHT_CLAW_SANDBOX",
        "full-auto",
        "CODEX_CLAW_SANDBOX",
        "CODEX_SANDBOX",
    )
    return _normalize_codex_sandbox(raw)


def _default_database_path(data_dir: Path) -> Path:
    legacy_database_path = data_dir / f"{LEGACY_APP_NAME}.db"
    if legacy_database_path.exists():
        return legacy_database_path
    return data_dir / f"{APP_NAME}.db"


def _resolve_optional_path(raw_value: Optional[str], base_dir: Path) -> Optional[Path]:
    if not raw_value:
        return None
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _require_mapping(value: Any, *, context: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{context} must be an object")
    return value


@dataclass(frozen=True)
class AgentSettings:
    agent_id: str
    name: str
    feishu_app_id: Optional[str]
    feishu_app_secret: Optional[str]
    feishu_verification_token: Optional[str]
    allow_from: str
    default_workspace_name: str
    default_cli_provider: str
    codex_model: Optional[str]
    codex_search: bool
    codex_sandbox: str
    skills_path: Optional[Path]
    mcp_config_path: Optional[Path]


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    host: str
    port: int
    data_dir: Path
    database_path: Path
    workspaces_dir: Path
    archive_enabled: bool
    archive_dir: Path
    archive_interval_seconds: int
    codex_bin: str
    codex_model: Optional[str]
    codex_search: bool
    codex_sandbox: str
    codex_timeout_min_seconds: int
    codex_timeout_max_seconds: int
    codex_timeout_per_char_ms: int
    codex_stall_timeout_seconds: int
    task_heartbeat_enabled: bool
    task_heartbeat_interval_seconds: int
    cron_enabled: bool
    cron_poll_interval_seconds: int
    status_heartbeat_enabled: bool
    status_heartbeat_seconds: int
    inbound_message_ttl_seconds: int
    default_cli_provider: str
    feishu_enabled: bool
    feishu_event_mode: str
    feishu_app_id: Optional[str]
    feishu_app_secret: Optional[str]
    feishu_verification_token: Optional[str]
    allow_from: str
    default_workspace_name: str
    agents: tuple[AgentSettings, ...]

    @classmethod
    def from_env(cls, base_dir: Optional[Path] = None) -> "Settings":
        root_dir = (
            _resolve_optional_path(_read_optional_str("LIGHT_CLAW_BASE_DIR"), Path.cwd())
            or (base_dir or Path.cwd()).resolve()
        )
        data_dir_raw = _read_optional_str("LIGHT_CLAW_DATA_DIR")
        data_dir = Path(data_dir_raw or str(root_dir / ".data")).expanduser().resolve()
        workspaces_dir = data_dir / "workspaces"
        archive_dir_raw = _read_optional_str("LIGHT_CLAW_ARCHIVE_DIR")
        archive_dir = Path(
            archive_dir_raw or str(root_dir.parent / f"{APP_NAME}-data")
        ).expanduser().resolve()
        database_path = _default_database_path(data_dir)
        codex_sandbox = _read_codex_sandbox()
        default_cli_provider = _read_str("DEFAULT_CLI_PROVIDER", "codex").lower()
        allow_from = _read_str("ALLOW_FROM", "*")
        default_workspace_name = _read_str("DEFAULT_WORKSPACE_NAME", "default")

        settings = cls(
            base_dir=root_dir,
            host=_read_str("HOST", "127.0.0.1"),
            port=_read_int("PORT", 8000),
            data_dir=data_dir,
            database_path=database_path,
            workspaces_dir=workspaces_dir,
            archive_enabled=_read_bool("LIGHT_CLAW_ARCHIVE_ENABLED", True),
            archive_dir=archive_dir,
            archive_interval_seconds=_read_int(
                "LIGHT_CLAW_ARCHIVE_INTERVAL_SECONDS", 12 * 60 * 60
            ),
            codex_bin=_read_str("CODEX_BIN", "codex"),
            codex_model=_read_optional_str("CODEX_MODEL"),
            codex_search=_read_bool("CODEX_SEARCH", False),
            codex_sandbox=codex_sandbox,
            codex_timeout_min_seconds=_read_int("CODEX_TIMEOUT_MIN_SECONDS", 180),
            codex_timeout_max_seconds=_read_int("CODEX_TIMEOUT_MAX_SECONDS", 900),
            codex_timeout_per_char_ms=_read_int("CODEX_TIMEOUT_PER_CHAR_MS", 80),
            codex_stall_timeout_seconds=_read_int("CODEX_STALL_TIMEOUT_SECONDS", 120),
            task_heartbeat_enabled=_read_bool("LIGHT_CLAW_TASK_HEARTBEAT_ENABLED", True),
            task_heartbeat_interval_seconds=_read_int(
                "LIGHT_CLAW_TASK_HEARTBEAT_INTERVAL_SECONDS", 30 * 60
            ),
            cron_enabled=_read_bool("LIGHT_CLAW_CRON_ENABLED", True),
            cron_poll_interval_seconds=_read_int(
                "LIGHT_CLAW_CRON_POLL_INTERVAL_SECONDS", 60
            ),
            status_heartbeat_enabled=_read_bool(
                "LIGHT_CLAW_STATUS_HEARTBEAT_ENABLED", True
            ),
            status_heartbeat_seconds=_read_int(
                "LIGHT_CLAW_STATUS_HEARTBEAT_SECONDS", 30
            ),
            inbound_message_ttl_seconds=_read_int(
                "LIGHT_CLAW_INBOUND_MESSAGE_TTL_SECONDS", 7 * 24 * 60 * 60
            ),
            default_cli_provider=default_cli_provider,
            feishu_enabled=_read_bool("FEISHU_ENABLED", True),
            feishu_event_mode=_read_feishu_event_mode(),
            feishu_app_id=_read_optional_str("FEISHU_APP_ID"),
            feishu_app_secret=_read_optional_str("FEISHU_APP_SECRET"),
            feishu_verification_token=_read_optional_str("FEISHU_VERIFICATION_TOKEN"),
            allow_from=allow_from,
            default_workspace_name=default_workspace_name,
            agents=tuple(
                _load_agents(
                    root_dir=root_dir,
                    feishu_enabled=_read_bool("FEISHU_ENABLED", True),
                    defaults={
                        "allow_from": allow_from,
                        "default_workspace_name": default_workspace_name,
                        "default_cli_provider": default_cli_provider,
                        "codex_model": _read_optional_str("CODEX_MODEL"),
                        "codex_search": _read_bool("CODEX_SEARCH", False),
                        "codex_sandbox": codex_sandbox,
                        "feishu_app_id": _read_optional_str("FEISHU_APP_ID"),
                        "feishu_app_secret": _read_optional_str("FEISHU_APP_SECRET"),
                        "feishu_verification_token": _read_optional_str(
                            "FEISHU_VERIFICATION_TOKEN"
                        ),
                    },
                )
            ),
        )
        settings.ensure_directories()
        settings.validate()
        return settings

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        if self.archive_enabled:
            self.archive_dir.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        if self.feishu_event_mode not in {"webhook", "long_connection"}:
            raise ValueError(
                "FEISHU_EVENT_MODE must be one of: webhook, long_connection"
            )
        if not self.default_cli_provider:
            raise ValueError("DEFAULT_CLI_PROVIDER must not be empty")
        if self.archive_interval_seconds <= 0:
            raise ValueError("LIGHT_CLAW_ARCHIVE_INTERVAL_SECONDS must be positive")
        if self.codex_stall_timeout_seconds <= 0:
            raise ValueError("CODEX_STALL_TIMEOUT_SECONDS must be positive")
        if self.task_heartbeat_interval_seconds <= 0:
            raise ValueError("LIGHT_CLAW_TASK_HEARTBEAT_INTERVAL_SECONDS must be positive")
        if self.cron_poll_interval_seconds <= 0:
            raise ValueError("LIGHT_CLAW_CRON_POLL_INTERVAL_SECONDS must be positive")
        if self.status_heartbeat_seconds <= 0:
            raise ValueError("LIGHT_CLAW_STATUS_HEARTBEAT_SECONDS must be positive")
        if self.inbound_message_ttl_seconds <= 0:
            raise ValueError(
                "LIGHT_CLAW_INBOUND_MESSAGE_TTL_SECONDS must be positive"
            )
        if not self.agents:
            raise ValueError("At least one agent configuration is required")

        seen_agent_ids: set[str] = set()
        seen_app_ids: set[str] = set()
        for agent in self.agents:
            if not agent.agent_id:
                raise ValueError("agent_id must not be empty")
            if agent.agent_id in seen_agent_ids:
                raise ValueError(f"Duplicate agent_id: {agent.agent_id}")
            seen_agent_ids.add(agent.agent_id)
            if not agent.default_cli_provider:
                raise ValueError(
                    f"DEFAULT_CLI_PROVIDER must not be empty for agent {agent.agent_id}"
                )
            if self.feishu_enabled:
                missing = []
                if not agent.feishu_app_id:
                    missing.append("FEISHU_APP_ID")
                if not agent.feishu_app_secret:
                    missing.append("FEISHU_APP_SECRET")
                if self.feishu_event_mode == "webhook" and not agent.feishu_verification_token:
                    missing.append("FEISHU_VERIFICATION_TOKEN")
                if missing:
                    raise ValueError(
                        "Missing required Feishu settings for agent {}: {}".format(
                            agent.agent_id,
                            ", ".join(missing),
                        )
                    )
                if agent.feishu_app_id in seen_app_ids:
                    raise ValueError(
                        f"Duplicate FEISHU_APP_ID detected: {agent.feishu_app_id}"
                    )
                if agent.feishu_app_id:
                    seen_app_ids.add(agent.feishu_app_id)

    def get_agent(self, agent_id: str) -> AgentSettings:
        for agent in self.agents:
            if agent.agent_id == agent_id:
                return agent
        raise KeyError(agent_id)

    def get_agent_by_app_id(self, app_id: str) -> Optional[AgentSettings]:
        for agent in self.agents:
            if agent.feishu_app_id == app_id:
                return agent
        return None

    @property
    def primary_agent(self) -> AgentSettings:
        return self.agents[0]


def _load_agents(
    *,
    root_dir: Path,
    feishu_enabled: bool,
    defaults: Mapping[str, Any],
) -> list[AgentSettings]:
    agents_file = _read_optional_str("LIGHT_CLAW_AGENTS_FILE")
    if not agents_file:
        return [
            AgentSettings(
                agent_id=DEFAULT_AGENT_ID,
                name="Default Agent",
                feishu_app_id=defaults.get("feishu_app_id"),
                feishu_app_secret=defaults.get("feishu_app_secret"),
                feishu_verification_token=defaults.get("feishu_verification_token"),
                allow_from=str(defaults["allow_from"]),
                default_workspace_name=str(defaults["default_workspace_name"]),
                default_cli_provider=str(defaults["default_cli_provider"]),
                codex_model=defaults.get("codex_model"),
                codex_search=bool(defaults["codex_search"]),
                codex_sandbox=str(defaults["codex_sandbox"]),
                skills_path=None,
                mcp_config_path=None,
            )
        ]

    agents_path = Path(agents_file).expanduser()
    if not agents_path.is_absolute():
        agents_path = root_dir / agents_path
    payload = json.loads(agents_path.read_text(encoding="utf-8"))
    entries = payload.get("agents") if isinstance(payload, Mapping) else payload
    if not isinstance(entries, list) or not entries:
        raise ValueError("LIGHT_CLAW_AGENTS_FILE must contain a non-empty agents list")

    agents: list[AgentSettings] = []
    for index, raw_entry in enumerate(entries, start=1):
        entry = _require_mapping(raw_entry, context=f"agents[{index}]")
        agent_id = str(entry.get("agent_id") or entry.get("id") or "").strip()
        if not agent_id:
            raise ValueError(f"agents[{index}].agent_id is required")
        name = str(entry.get("name") or agent_id).strip() or agent_id
        allow_from = str(entry.get("allow_from") or defaults["allow_from"]).strip()
        default_workspace_name = str(
            entry.get("default_workspace_name") or defaults["default_workspace_name"]
        ).strip()
        default_cli_provider = str(
            entry.get("default_cli_provider") or defaults["default_cli_provider"]
        ).strip().lower()
        codex_model = entry.get("codex_model", defaults.get("codex_model"))
        codex_search = bool(entry.get("codex_search", defaults["codex_search"]))
        codex_sandbox = _normalize_codex_sandbox(
            str(entry.get("codex_sandbox") or defaults["codex_sandbox"])
        )
        skills_path = _resolve_optional_path(
            _coerce_optional_str(entry.get("skills_path")),
            root_dir,
        )
        mcp_config_path = _resolve_optional_path(
            _coerce_optional_str(entry.get("mcp_config_path")),
            root_dir,
        )
        agents.append(
            AgentSettings(
                agent_id=agent_id,
                name=name,
                feishu_app_id=_coerce_optional_str(
                    entry.get("feishu_app_id") or entry.get("app_id")
                ),
                feishu_app_secret=_coerce_optional_str(
                    entry.get("feishu_app_secret") or entry.get("app_secret")
                ),
                feishu_verification_token=_coerce_optional_str(
                    entry.get("feishu_verification_token")
                    or entry.get("verification_token")
                ),
                allow_from=allow_from or "*",
                default_workspace_name=default_workspace_name or "default",
                default_cli_provider=default_cli_provider or "codex",
                codex_model=_coerce_optional_str(codex_model),
                codex_search=codex_search,
                codex_sandbox=codex_sandbox,
                skills_path=skills_path,
                mcp_config_path=mcp_config_path,
            )
        )
    if not feishu_enabled:
        return agents
    return agents


def _coerce_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
