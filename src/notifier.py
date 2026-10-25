"""
AtmosTune Notifier & Logging Subsystem.

Provides:
1. Rotating file logger preventing disk bloat.
2. Formatted terminal console output.
3. Native desktop toast notification with silent fallback.
"""

from __future__ import annotations

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional


def setup_logger(
    name: str = "AtmosTune",
    log_file: str = "./logs/atmostune.log",
    log_level: str = "INFO",
    max_bytes: int = 2097152,
    backup_count: int = 3,
    verbose: bool = False,
) -> logging.Logger:
    """Configures rotating file handler and colored console handler."""
    logger = logging.getLogger(name)
    level = logging.DEBUG if verbose else getattr(logging, log_level.upper(), logging.INFO)
    logger.setLevel(level)

    # Avoid duplicate handlers if re-initialized
    if logger.handlers:
        return logger

    # Ensure log directory exists
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Rotating File Handler
    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)

    # 2. Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(level)
    logger.addHandler(console_handler)

    return logger


class AtmosNotifier:
    """Dispatches native desktop notifications and audits operations."""

    def __init__(self, config: Optional[Dict[str, Any]] = None, logger: Optional[logging.Logger] = None) -> None:
        self.config = config or {}
        self.notify_cfg = self.config.get("notifications", {})
        self.telegram_cfg = self.config.get("telegram", {})
        self.enabled = self.notify_cfg.get("enabled", True)
        self.telegram_enabled = self.telegram_cfg.get("enabled", False)
        self.app_name = self.notify_cfg.get("app_name", "AtmosTune")
        self.timeout = self.notify_cfg.get("timeout_sec", 6)
        self.logger = logger or logging.getLogger("AtmosTune.Notifier")

        # Suppress harmless plyer Win32 balloon_tip thread failures on headless/terminal environments
        self._install_thread_guard()

    def _install_thread_guard(self) -> None:
        """Installs exception filter to suppress non-fatal GUI notification thread errors."""
        import threading
        original_hook = threading.excepthook

        def _safe_excepthook(args):
            exc_str = str(args.exc_value)
            thread_name = str(args.thread.name)
            if "Shell_NotifyIconW" in exc_str or "balloon_tip" in thread_name:
                self.logger.debug("Silently caught background OS balloon_tip exception: %s", exc_str)
                return
            if original_hook:
                original_hook(args)

        threading.excepthook = _safe_excepthook

    def notify(
        self,
        title: str,
        message: str,
        app_icon: Optional[str] = None,
    ) -> bool:
        """
        Sends an OS desktop toast notification.
        Fails safely without raising exceptions if notification daemon is unavailable.
        """
        if not self.enabled:
            return False

        try:
            from plyer import notification
            notification.notify(
                title=f"{self.app_name} • {title}",
                message=message,
                app_name=self.app_name,
                app_icon=app_icon,
                timeout=self.timeout,
            )
            self.logger.debug("Desktop toast dispatched: '%s' - '%s'", title, message)
            return True
        except Exception as exc:
            self.logger.warning("Desktop notification skipped (%s)", exc)
            return False

    def send_telegram(self, message: str) -> bool:
        """
        Sends an instant notification to all authorized Telegram chat IDs via Telegram Bot API.
        Does not require running a long-lived bot process; uses lightweight HTTP POST.
        """
        token = os.getenv("TELEGRAM_BOT_TOKEN", self.telegram_cfg.get("bot_token", "")).strip()
        chat_ids = self.telegram_cfg.get("allowed_chat_ids", [])

        if not token or not chat_ids:
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        dispatched = 0

        for chat_id in chat_ids:
            try:
                import requests
                payload = {
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                }
                resp = requests.post(url, json=payload, timeout=5)
                if resp.status_code == 200:
                    dispatched += 1
                else:
                    self.logger.warning("Telegram notification to %s failed (HTTP %d)", chat_id, resp.status_code)
            except Exception as exc:
                self.logger.debug("Telegram dispatch error for chat %s: %s", chat_id, exc)

        if dispatched > 0:
            self.logger.info("Dispatched Telegram alert to %d chat(s).", dispatched)
            return True
        return False

    def notify_rebuild_success(
        self,
        mood_name: str,
        condition: str,
        temperature: float,
        time_block: str,
        track_count: int,
        lead_track: Optional[str] = None,
        playlist_url: Optional[str] = None,
    ) -> bool:
        """Helper to send a formatted atmospheric playlist refresh alert to Desktop and Telegram."""
        title = f"{mood_name}"
        desc = f"Curated {track_count} tracks for {condition} at {time_block} ({temperature:+.1f}°C)."
        if lead_track:
            desc += f" Starter: {lead_track}"

        # 1. Desktop Toast
        toast_ok = self.notify(title=title, message=desc)

        # 2. Telegram Broadcast (if enabled)
        if self.telegram_cfg.get("notify_on_rebuild", True):
            tg_msg = (
                f"🎧 *AtmosTune Playlist Updated!*\n\n"
                f"🌦️ *Atmosphere*: {condition} ({temperature:+.1f}°C)\n"
                f"⏰ *Diurnal Block*: {time_block}\n"
                f"🎨 *Active Mood*: *{mood_name}*\n"
                f"🎵 *Curated*: {track_count} tracks\n"
            )
            if lead_track:
                tg_msg += f"▶️ *Lead Track*: {lead_track}\n"
            if playlist_url:
                tg_msg += f"\n🔗 [Open Playlist on Spotify]({playlist_url})"

            self.send_telegram(tg_msg)

        return toast_ok
