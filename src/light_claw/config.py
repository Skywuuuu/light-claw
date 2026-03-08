from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


APP_NAME = "light-claw"
LEGACY_APP_NAME = "codex-claw"


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


def _read_codex_sandbox() -> str:
    raw = _read_str(
        "LIGHT_CLAW_SANDBOX",
        "full-auto",
        "CODEX_CLAW_SANDBOX",
        "CODEX_SANDBOX",
    ).strip().lower()
    mapping = {
        "full-auto": "full-auto",
        "workspace-write": "full-auto",
        "read-only": "full-auto",
        "seatbelt": "full-auto",
        "none": "none",
        "danger-full-access": "none",
    }
    return mapping.get(raw, "full-auto")


def _default_database_path(data_dir: Path) -> Path:
    legacy_database_path = data_dir / f"{LEGACY_APP_NAME}.db"
    if legacy_database_path.exists():
        return legacy_database_path
    return data_dir / f"{APP_NAME}.db"


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    data_dir: Path
    database_path: Path
    workspaces_dir: Path
    codex_bin: str
    codex_model: Optional[str]
    codex_search: bool
    codex_sandbox: str
    codex_timeout_min_seconds: int
    codex_timeout_max_seconds: int
    codex_timeout_per_char_ms: int
    default_cli_provider: str
    feishu_enabled: bool
    feishu_event_mode: str
    feishu_app_id: Optional[str]
    feishu_app_secret: Optional[str]
    feishu_verification_token: Optional[str]
    allow_from: str
    default_workspace_name: str

    @classmethod
    def from_env(cls, base_dir: Optional[Path] = None) -> "Settings":
        root_dir = (base_dir or Path.cwd()).resolve()
        data_dir_raw = _read_optional_str("LIGHT_CLAW_DATA_DIR")
        data_dir = Path(data_dir_raw or str(root_dir / ".data")).expanduser().resolve()
        workspaces_dir = data_dir / "workspaces"
        database_path = _default_database_path(data_dir)
        codex_sandbox = _read_codex_sandbox()

        settings = cls(
            host=_read_str("HOST", "0.0.0.0"),
            port=_read_int("PORT", 8000),
            data_dir=data_dir,
            database_path=database_path,
            workspaces_dir=workspaces_dir,
            codex_bin=_read_str("CODEX_BIN", "codex"),
            codex_model=_read_optional_str("CODEX_MODEL"),
            codex_search=_read_bool("CODEX_SEARCH", False),
            codex_sandbox=codex_sandbox,
            codex_timeout_min_seconds=_read_int("CODEX_TIMEOUT_MIN_SECONDS", 180),
            codex_timeout_max_seconds=_read_int("CODEX_TIMEOUT_MAX_SECONDS", 900),
            codex_timeout_per_char_ms=_read_int("CODEX_TIMEOUT_PER_CHAR_MS", 80),
            default_cli_provider=_read_str("DEFAULT_CLI_PROVIDER", "codex").lower(),
            feishu_enabled=_read_bool("FEISHU_ENABLED", True),
            feishu_event_mode=_read_feishu_event_mode(),
            feishu_app_id=_read_optional_str("FEISHU_APP_ID"),
            feishu_app_secret=_read_optional_str("FEISHU_APP_SECRET"),
            feishu_verification_token=_read_optional_str("FEISHU_VERIFICATION_TOKEN"),
            allow_from=_read_str("ALLOW_FROM", "*"),
            default_workspace_name=_read_str("DEFAULT_WORKSPACE_NAME", "default"),
        )
        settings.ensure_directories()
        settings.validate()
        return settings

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def validate(self) -> None:
        if self.feishu_enabled:
            if self.feishu_event_mode not in {"webhook", "long_connection"}:
                raise ValueError(
                    "FEISHU_EVENT_MODE must be one of: webhook, long_connection"
                )
            missing = []
            if not self.feishu_app_id:
                missing.append("FEISHU_APP_ID")
            if not self.feishu_app_secret:
                missing.append("FEISHU_APP_SECRET")
            if (
                self.feishu_event_mode == "webhook"
                and not self.feishu_verification_token
            ):
                missing.append("FEISHU_VERIFICATION_TOKEN")
            if missing:
                raise ValueError(
                    "Missing required Feishu settings: " + ", ".join(missing)
                )
        if not self.default_cli_provider:
            raise ValueError("DEFAULT_CLI_PROVIDER must not be empty")
