from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Callable, Optional

from .archive_sync import sync_all_workspaces
from .store import StateStore


log = logging.getLogger("light_claw.archive")
ARCHIVE_DAILY_TIME_SETTING_KEY = "archive.daily_time"


def normalize_daily_time(raw_value: str | None) -> str | None:
    if raw_value is None:
        return None
    value = raw_value.strip()
    if not value:
        return None
    parts = value.split(":", maxsplit=1)
    if len(parts) != 2 or not all(part.isdigit() for part in parts):
        raise ValueError("daily archive time must use HH:MM in 24-hour time")
    hour = int(parts[0])
    minute = int(parts[1])
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("daily archive time must use HH:MM in 24-hour time")
    return "{:02d}:{:02d}".format(hour, minute)


def compute_next_daily_run_at(
    now_ts: float,
    daily_time: str,
    timezone: tzinfo | None = None,
) -> float:
    normalized = normalize_daily_time(daily_time)
    if normalized is None:
        raise ValueError("daily archive time is required")
    hour, minute = [int(part) for part in normalized.split(":", maxsplit=1)]
    tz = timezone or datetime.fromtimestamp(now_ts).astimezone().tzinfo
    now = datetime.fromtimestamp(now_ts, tz=tz)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate.timestamp()


class WorkspaceArchiveService:
    """Mirror workspace contents into an external archive directory."""

    def __init__(
        self,
        store: StateStore,
        archive_root: Path,
        interval_seconds: int,
        inbound_message_ttl_seconds: int = 0,
        on_sync_success: Optional[Callable[[], None]] = None,
        on_sync_error: Optional[Callable[[Exception], None]] = None,
    ) -> None:
        self.store = store
        self.archive_root = archive_root.resolve()
        self.interval_seconds = interval_seconds
        self.inbound_message_ttl_seconds = inbound_message_ttl_seconds
        self.on_sync_success = on_sync_success
        self.on_sync_error = on_sync_error
        self._task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._reschedule_event = asyncio.Event()
        self.last_success_at: Optional[float] = None
        self.last_error: Optional[str] = None
        stored_daily_time = self.store.get_app_setting(ARCHIVE_DAILY_TIME_SETTING_KEY)
        try:
            self.daily_time = normalize_daily_time(stored_daily_time)
        except ValueError:
            log.warning("ignore invalid stored archive.daily_time: %s", stored_daily_time)
            self.daily_time = None
        self.next_run_at = self._compute_next_run_at(time.time())

    async def start(self) -> None:
        """Start the background archive loop and run an initial sync."""

        if self._task is not None:
            return
        self.archive_root.mkdir(parents=True, exist_ok=True)
        await self.run_once()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        """Stop the background archive loop."""

        self._stop_event.set()
        self._reschedule_event.set()
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> None:
        """Synchronize all known workspaces to the archive directory."""

        try:
            await asyncio.to_thread(
                sync_all_workspaces,
                store=self.store,
                archive_root=self.archive_root,
                inbound_message_ttl_seconds=self.inbound_message_ttl_seconds,
            )
        except Exception as exc:
            self.last_error = str(exc)
            if self.on_sync_error is not None:
                self.on_sync_error(exc)
            raise
        self.last_success_at = time.time()
        self.last_error = None
        self.next_run_at = self._compute_next_run_at(self.last_success_at)
        if self.on_sync_success is not None:
            self.on_sync_success()

    def update_daily_time(self, raw_value: str) -> str:
        normalized = normalize_daily_time(raw_value)
        if normalized is None:
            raise ValueError("daily archive time is required")
        self.store.set_app_setting(ARCHIVE_DAILY_TIME_SETTING_KEY, normalized)
        self.daily_time = normalized
        self.next_run_at = self._compute_next_run_at(time.time())
        self._reschedule_event.set()
        return normalized

    def _compute_next_run_at(self, now_ts: float) -> float:
        if self.daily_time:
            return compute_next_daily_run_at(now_ts, self.daily_time)
        return now_ts + self.interval_seconds

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            now = time.time()
            next_run_at = self._compute_next_run_at(now)
            self.next_run_at = next_run_at
            timeout = max(next_run_at - now, 0.0)
            try:
                await asyncio.wait_for(self._reschedule_event.wait(), timeout=timeout)
                self._reschedule_event.clear()
                if self._stop_event.is_set():
                    break
                continue
            except asyncio.TimeoutError:
                try:
                    await self.run_once()
                except Exception:
                    log.exception("workspace archive sync failed")
