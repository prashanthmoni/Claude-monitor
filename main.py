#!/usr/bin/env python3
"""
Claude Usage Monitor — macOS Menu Bar Application.

A lightweight menu bar widget that shows real-time Claude Pro usage
(claude.ai messages + Claude Code CLI activity) with color-coded
indicators and system notifications at configurable thresholds.

Built with `rumps` for a native macOS experience and minimal resource
footprint (~30-50 MB RSS).

Usage:
    python3 main.py             # Normal launch
    python3 main.py --setup     # Re-enter credentials
"""

import argparse
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

import rumps

import config
import usage_tracker

# ─── Logging Setup ───────────────────────────────────────────────────
# Rotating log file: max 5 MB, keeps 1 backup (.log.1).

_log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

_file_handler = RotatingFileHandler(
    config.LOG_FILE,
    maxBytes=config.LOG_MAX_BYTES,
    backupCount=config.LOG_BACKUP_COUNT,
)
_file_handler.setFormatter(_log_formatter)

logging.basicConfig(level=logging.INFO, handlers=[_file_handler])
logger = logging.getLogger(__name__)


# ─── Credential Prompt ───────────────────────────────────────────────


def prompt_for_credentials() -> bool:
    """Interactive prompt that asks the user to paste their session cookie.

    The cookie is stored securely in macOS Keychain. Returns True on
    success, False if the user cancels or storage fails.
    """
    print("\n╔══════════════════════════════════════════════════════╗")
    print("║       Claude Usage Monitor — First-Time Setup       ║")
    print("╠══════════════════════════════════════════════════════╣")
    print("║ To monitor your Claude usage, the app needs your    ║")
    print("║ claude.ai session cookie.                           ║")
    print("║                                                     ║")
    print("║ How to get it:                                      ║")
    print("║  1. Open https://claude.ai in your browser          ║")
    print("║  2. Open DevTools (Cmd+Option+I) → Application tab  ║")
    print("║  3. Under Cookies → https://claude.ai, copy the     ║")
    print("║     full cookie string (all name=value pairs)       ║")
    print("║  4. Paste it below                                  ║")
    print("╚══════════════════════════════════════════════════════╝\n")

    try:
        cookie = input("Session cookie (paste and press Enter): ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.")
        return False

    if not cookie:
        print("No cookie provided — setup cancelled.")
        return False

    if usage_tracker.store_session_cookie(cookie):
        print("Cookie stored securely in macOS Keychain.")
        return True
    else:
        print("ERROR: Failed to store cookie. Check Keychain access.")
        return False


# ─── Menu Bar Application ───────────────────────────────────────────


class ClaudeMonitorApp(rumps.App):
    """Main menu bar application.

    Lifecycle:
      1. ``__init__`` builds the menu structure.
      2. ``on_tick`` fires every ``REFRESH_INTERVAL`` seconds via a
         ``rumps.Timer`` to poll fresh data on a background thread.
      3. ``_update_ui`` applies the latest snapshot to menu items
         (called on the main thread via ``rumps.Timer``).
    """

    def __init__(self) -> None:
        # Start with a generic title; updated after first data fetch.
        super().__init__(
            name=config.APP_NAME,
            title="C ⏳",
            quit_button=None,  # We add a custom Quit below.
        )

        # ── Build Menu Items ──
        self.header_item = rumps.MenuItem("Claude Usage Monitor")
        self.separator1 = rumps.separator

        self.overall_item = rumps.MenuItem("Overall: —")
        self.separator2 = rumps.separator

        self.web_header = rumps.MenuItem("claude.ai")
        self.web_messages = rumps.MenuItem("  Messages: —")
        self.web_reset = rumps.MenuItem("  Resets in: —")
        self.separator3 = rumps.separator

        self.cli_header = rumps.MenuItem("Claude Code CLI")
        self.cli_status = rumps.MenuItem("  Status: —")
        self.cli_last = rumps.MenuItem("  Last used: —")
        self.separator4 = rumps.separator

        self.refresh_item = rumps.MenuItem(
            "🔄 Refresh Now", callback=self.on_refresh_clicked
        )

        # Preferences sub-menu
        self.prefs_menu = rumps.MenuItem("⚙️ Preferences...")
        self.prefs_interval = rumps.MenuItem(
            f"  Refresh Interval: {config.REFRESH_INTERVAL // 60}m",
        )
        self.prefs_alerts = rumps.MenuItem(
            f"  Warning: {int(config.WARNING_THRESHOLD * 100)}% / "
            f"Critical: {int(config.CRITICAL_THRESHOLD * 100)}%",
        )
        self.prefs_reauth = rumps.MenuItem(
            "  Re-authenticate", callback=self.on_reauth_clicked
        )
        self.prefs_menu.update([
            self.prefs_interval,
            self.prefs_alerts,
            self.prefs_reauth,
        ])

        self.quit_item = rumps.MenuItem("❌ Quit", callback=self.on_quit)

        self.menu = [
            self.header_item,
            self.separator1,
            self.overall_item,
            self.separator2,
            self.web_header,
            self.web_messages,
            self.web_reset,
            self.separator3,
            self.cli_header,
            self.cli_status,
            self.cli_last,
            self.separator4,
            self.refresh_item,
            self.prefs_menu,
            self.quit_item,
        ]

        # ── State ──
        self._latest_snapshot: Dict[str, Any] = {}
        self._fetch_in_progress = False
        # Track which alert tiers have already fired so we don't spam.
        self._alerted_warning = False
        self._alerted_critical = False

        # ── Background Timer ──
        # rumps.Timer calls back on the main run-loop, so we launch the
        # actual network call in a daemon thread from the callback.
        self._timer = rumps.Timer(self._on_timer_tick, config.REFRESH_INTERVAL)
        self._timer.start()

        # Kick off an immediate first fetch.
        self._start_background_fetch()

    # ── Timer / Fetch ────────────────────────────────────────────────

    def _on_timer_tick(self, _sender: Any) -> None:
        """Called by rumps.Timer on the main thread every REFRESH_INTERVAL."""
        self._start_background_fetch()

    def _start_background_fetch(self) -> None:
        """Spawn a daemon thread to fetch usage data without blocking UI."""
        if self._fetch_in_progress:
            return
        self._fetch_in_progress = True
        t = threading.Thread(target=self._do_fetch, daemon=True)
        t.start()

    def _do_fetch(self) -> None:
        """Network-bound work that runs on a background thread.

        After fetching, schedules a UI update on the main thread by
        writing to the shared snapshot and setting a short Timer to
        trigger ``_apply_snapshot``.
        """
        try:
            snapshot = usage_tracker.refresh_usage_data()
            self._latest_snapshot = snapshot
            logger.info(
                "Usage data refreshed — overall %.1f%%",
                snapshot.get("overall_percentage", 0) * 100,
            )
        except Exception:
            logger.exception("Background fetch failed")
        finally:
            self._fetch_in_progress = False
            # Schedule a one-shot timer to update UI on main thread.
            rumps.Timer(self._apply_snapshot, 0.1).start()

    def _apply_snapshot(self, _sender: Any = None) -> None:
        """Update all menu items from ``_latest_snapshot``.

        Must run on the main thread (called via rumps.Timer one-shot).
        """
        # Stop the one-shot timer immediately.
        if _sender is not None:
            _sender.stop()

        snap = self._latest_snapshot
        if not snap:
            return

        overall_pct = snap.get("overall_percentage", 0)
        web = snap.get("claude_web", {})
        cli = snap.get("claude_code", {})

        # ── Title & Color ──
        pct_display = int(overall_pct * 100)
        icon = self._color_icon(overall_pct)
        self.title = f"{icon} {pct_display}%"

        # ── Overall ──
        alert_symbol = ""
        if overall_pct >= config.CRITICAL_THRESHOLD:
            alert_symbol = " 🔴"
        elif overall_pct >= config.WARNING_THRESHOLD:
            alert_symbol = " ⚠️"
        self.overall_item.title = f"📊 Overall: {pct_display}%{alert_symbol}"

        # ── Web Messages ──
        used = web.get("messages_used")
        limit = web.get("messages_limit")
        if used is not None and limit is not None:
            web_pct = int((used / limit) * 100) if limit else 0
            self.web_messages.title = (
                f"  Messages: {used}/{limit} ({web_pct}%)"
            )
        else:
            self.web_messages.title = "  Messages: unavailable"

        # ── Reset Time ──
        raw_reset = web.get("reset_time")
        reset_dt = usage_tracker.parse_reset_time(raw_reset)
        self.web_reset.title = (
            f"  Resets in: {usage_tracker.time_until_reset(reset_dt)}"
        )

        # ── CLI ──
        cli_status = cli.get("status", "unknown")
        status_icon = {"active": "✓", "idle": "—", "unknown": "?"}.get(
            cli_status, "?"
        )
        self.cli_status.title = (
            f"  Status: {cli_status.capitalize()} {status_icon}"
        )

        last_activity = cli.get("last_activity")
        if last_activity:
            self.cli_last.title = (
                f"  Last used: {self._relative_time(last_activity)}"
            )
        else:
            self.cli_last.title = "  Last used: —"

        # ── Notifications ──
        self._maybe_notify(overall_pct, used, limit, reset_dt)

    # ── Notifications ────────────────────────────────────────────────

    def _maybe_notify(
        self,
        pct: float,
        used: Optional[int],
        limit: Optional[int],
        reset_dt: Optional[datetime],
    ) -> None:
        """Send macOS notifications when thresholds are crossed.

        Each threshold only fires once per reset cycle. When the
        percentage drops (after a reset), flags are cleared.
        """
        reset_str = usage_tracker.time_until_reset(reset_dt)
        remaining = (limit - used) if (limit and used is not None) else "?"

        if pct >= config.CRITICAL_THRESHOLD and not self._alerted_critical:
            rumps.notification(
                title="🔴 Claude Usage Critical!",
                subtitle=f"Only {remaining} messages left.",
                message=f"Resets in {reset_str}.",
            )
            self._alerted_critical = True
            self._save_alert_timestamp()
            logger.info("Critical notification sent (%.0f%%)", pct * 100)

        elif pct >= config.WARNING_THRESHOLD and not self._alerted_warning:
            rumps.notification(
                title="⚠️ Claude Usage at 80%",
                subtitle=f"You have {remaining} messages remaining.",
                message=f"Resets in {reset_str}.",
            )
            self._alerted_warning = True
            self._save_alert_timestamp()
            logger.info("Warning notification sent (%.0f%%)", pct * 100)

        # Reset flags when usage drops below warning (after quota reset).
        if pct < config.WARNING_THRESHOLD:
            self._alerted_warning = False
            self._alerted_critical = False

    def _save_alert_timestamp(self) -> None:
        """Persist the time of the last alert into the cache file."""
        try:
            cache = usage_tracker.load_cache()
            cache["last_alert_sent"] = datetime.now(timezone.utc).isoformat()
            usage_tracker.save_cache(cache)
        except Exception:
            logger.exception("Failed to save alert timestamp")

    # ── Menu Callbacks ───────────────────────────────────────────────

    @rumps.clicked("🔄 Refresh Now")
    def on_refresh_clicked(self, _sender: Any = None) -> None:
        """User clicked 'Refresh Now'."""
        logger.info("Manual refresh requested")
        self._start_background_fetch()

    def on_reauth_clicked(self, _sender: Any = None) -> None:
        """Open a terminal window to re-enter credentials.

        Since rumps doesn't support inline text input, we open a new
        Terminal window running this script with ``--setup``.
        """
        os.system(
            f'osascript -e \'tell application "Terminal" to do script '
            f'"python3 {os.path.abspath(__file__)} --setup"\''
        )

    def on_quit(self, _sender: Any = None) -> None:
        """Clean up and exit."""
        logger.info("Application quitting")
        self._timer.stop()
        rumps.quit_application()

    # ── Helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _color_icon(pct: float) -> str:
        """Return a colored indicator based on usage percentage."""
        if pct >= config.CRITICAL_THRESHOLD:
            return config.APP_ICON_RED
        if pct >= config.WARNING_THRESHOLD:
            return config.APP_ICON_YELLOW
        if pct >= 0.60:
            return config.APP_ICON_YELLOW
        return config.APP_ICON_GREEN

    @staticmethod
    def _relative_time(iso_str: str) -> str:
        """Convert an ISO timestamp to a human-readable relative string."""
        try:
            from dateutil import parser as dp
            dt = dp.parse(iso_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            delta = datetime.now(timezone.utc) - dt
            secs = int(delta.total_seconds())
            if secs < 60:
                return "just now"
            if secs < 3600:
                return f"{secs // 60}m ago"
            if secs < 86400:
                return f"{secs // 3600}h ago"
            return f"{secs // 86400}d ago"
        except Exception:
            return iso_str


# ─── Entry Point ─────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description=config.APP_NAME)
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Run credential setup wizard",
    )
    args = parser.parse_args()

    if args.setup:
        prompt_for_credentials()
        return

    # On first run, check whether credentials exist.
    cookie = usage_tracker.get_session_cookie()
    if not cookie:
        print("No credentials found. Starting setup wizard...")
        if not prompt_for_credentials():
            print("Setup incomplete — exiting.")
            sys.exit(1)

    logger.info("Starting %s", config.APP_NAME)
    app = ClaudeMonitorApp()
    app.run()


if __name__ == "__main__":
    main()
